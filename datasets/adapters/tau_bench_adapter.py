#!/usr/bin/env python3
"""Convert τ-bench tasks to voice-agent two-agent bench YAML.

τ-bench few-shot JSONL contains pre-recorded conversations (messages_display).
We extract: first user utterance as caller goal, user_id from tool call,
then build rich answerer context from the database.

Usage:
    python datasets/adapters/tau_bench_adapter.py [--domain retail|airline|both] [--n 20]
    voice-agent bench --mode two-agent --dataset scenarios/tau_bench_retail.yaml
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

DATASETS_DIR = Path(__file__).parent.parent
SCENARIOS_DIR = DATASETS_DIR.parent / "scenarios"

_RETAIL_POLICY_SUMMARY = (
    "Company policy: Returns accepted within 30 days for delivered items. "
    "Exchanges allowed for delivered items within 30 days. "
    "Order modifications (items, address, payment) only for pending orders. "
    "Cancellations only for pending orders. "
    "Verify customer identity via name+zip OR email before any account changes."
)

_AIRLINE_POLICY_SUMMARY = (
    "Airline policy: Reservations can be cancelled for a full refund up to 24h before departure. "
    "Flight changes are allowed for a fee depending on fare class. "
    "Basic economy tickets cannot be upgraded. "
    "Free baggage: gold=3 bags, silver=2 bags, bronze=1 bag. "
    "Verify passenger identity (name + DOB or email) before any changes."
)


def load_jsonl(path: Path) -> list[dict]:
    tasks = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                tasks.append(json.loads(line))
    return tasks


def load_json(path: Path) -> dict | list:
    with open(path) as f:
        return json.load(f)


def _parse_messages(display: str) -> list[tuple[str, str]]:
    """Parse messages_display string into [(speaker, text), ...] tuples.

    Handles multiline assistant messages that include 'tool:' continuations.
    """
    turns = re.split(r"\n(?=user:|assistant:|tool:|system:)", display.strip())
    parsed = []
    for turn in turns:
        if turn.startswith("user:"):
            text = turn[len("user:"):].strip()
            parsed.append(("user", text))
        elif turn.startswith("assistant:"):
            text = turn[len("assistant:"):].strip()
            # Remove "None\ntool: ..." pattern — these are tool calls embedded in assistant turn
            text = re.sub(r"^None\s*\n?", "", text).strip()
            if text and not text.startswith("{"):
                parsed.append(("assistant", text))
        elif turn.startswith("tool:"):
            text = turn[len("tool:"):].strip()
            parsed.append(("tool", text))
    return parsed


def _extract_user_id(turns: list[tuple[str, str]]) -> str:
    """Extract user_id from tool returns.

    Two patterns:
    1. Plain string: 'isabella_lopez_6490' (retail identity lookup)
    2. JSON with user_id key: '{"reservation_id": "...", "user_id": "aarav_..."}'
    """
    for speaker, text in turns:
        if speaker != "tool":
            continue
        stripped = text.strip()
        if not stripped:
            continue

        # Pattern 1: plain user_id string (no braces, not an error)
        if not stripped.startswith("{") and not stripped.startswith("[") and "Error" not in stripped:
            candidate = stripped.split()[0]
            # Looks like a user_id if it contains underscores and digits
            if re.match(r"^[a-z]+_[a-z]+_\d+$", candidate):
                return candidate

        # Pattern 2: JSON object with user_id field
        if stripped.startswith("{"):
            try:
                obj = json.loads(stripped)
                if isinstance(obj, dict) and "user_id" in obj:
                    return obj["user_id"]
            except json.JSONDecodeError:
                pass

    return ""


def _extract_first_user_message(turns: list[tuple[str, str]]) -> str:
    for speaker, text in turns:
        if speaker == "user":
            return text
    return ""


def _extract_last_assistant_message(turns: list[tuple[str, str]]) -> str:
    last = ""
    for speaker, text in turns:
        if speaker == "assistant" and text:
            last = text
    return last


def _success_phrases_from_resolution(resolution: str) -> list[str]:
    """Heuristically extract 1-2 success phrases from the final assistant turn."""
    lower = resolution.lower()
    candidates = [
        ("successfully changed", "successfully changed"),
        ("successfully modified", "successfully modified"),
        ("successfully cancelled", "successfully cancelled"),
        ("successfully canceled", "successfully canceled"),
        ("successfully exchanged", "successfully exchanged"),
        ("successfully returned", "successfully returned"),
        ("successfully updated", "successfully updated"),
        ("has been cancelled", "has been cancelled"),
        ("has been processed", "has been processed"),
        ("has been modified", "has been modified"),
        ("has been updated", "has been updated"),
        ("have been cancelled", "have been cancelled"),
        ("order #", "order #"),
        ("reservation ", "reservation"),
        ("refund", "refund"),
    ]
    found = []
    for pattern, phrase in candidates:
        if pattern in lower and phrase not in found:
            found.append(phrase)
        if len(found) >= 2:
            break
    return found if found else ["done", "taken care of"]


def _retail_user_context(user_id: str, users: dict, orders: dict) -> str:
    user = users.get(user_id, {})
    if not user:
        return f"No account data found for user_id={user_id}"

    name = f"{user['name']['first_name']} {user['name']['last_name']}"
    email = user.get("email", "")
    addr = user.get("address", {})
    zip_code = addr.get("zip", "")
    state = addr.get("state", "")
    city = addr.get("city", "")
    address_str = f"{addr.get('address1', '')}, {city}, {state} {zip_code}"

    lines = [
        f"Account ID: {user_id}",
        f"Name: {name}",
        f"Email: {email}",
        f"Address: {address_str}",
    ]

    pmethods = list(user.get("payment_methods", {}).values())
    for pm in pmethods[:2]:
        if pm.get("source") == "credit_card":
            lines.append(f"Payment: {pm.get('brand', 'card')} ending in {pm.get('last_four', '????')} (ID: {pm.get('id', '')})")
        else:
            lines.append(f"Payment: {pm.get('source', 'unknown')} (ID: {pm.get('id', '')})")

    for oid in user.get("orders", [])[:4]:
        order = orders.get(oid, {})
        if not order:
            continue
        items = "; ".join(
            f"{it['name']} (${it.get('price', 0):.2f}, item_id={it.get('item_id', '')})"
            for it in order.get("items", [])[:3]
        )
        lines.append(f"Order {oid} [{order.get('status', '?')}]: {items}")

    return "\n".join(lines)


def _airline_user_context(user_id: str, users: dict, reservations: dict, flights: dict) -> str:
    user = users.get(user_id, {})
    if not user:
        return f"No account data found for user_id={user_id}"

    name = f"{user['name']['first_name']} {user['name']['last_name']}"
    email = user.get("email", "")
    dob = user.get("dob", "")
    membership = user.get("membership", "none")

    lines = [
        f"Account ID: {user_id}",
        f"Name: {name}",
        f"Email: {email}",
        f"DOB: {dob}",
        f"Membership: {membership}",
    ]

    pmethods = list(user.get("payment_methods", {}).values())
    for pm in pmethods[:2]:
        if pm.get("source") == "credit_card":
            lines.append(f"Payment: {pm.get('brand', 'card')} ending in {pm.get('last_four', '????')} (ID: {pm.get('id', '')})")
        elif pm.get("source") == "certificate":
            lines.append(f"Payment: travel certificate ${ pm.get('amount', 0)} (ID: {pm.get('id', '')})")

    for rid in user.get("reservations", [])[:3]:
        res = reservations.get(rid, {})
        if not res:
            continue
        route_parts = []
        for fl in res.get("flights", []):
            route_parts.append(f"{fl.get('origin','?')}->{fl.get('destination','?')} on {fl.get('date','')} ({fl.get('flight_number','')})")
        route = ", ".join(route_parts)
        passengers = "; ".join(
            f"{p.get('first_name','')} {p.get('last_name','')} DOB {p.get('dob','')}"
            for p in res.get("passengers", [])[:2]
        )
        lines.append(
            f"Reservation {rid} [{res.get('cabin','?')}/{res.get('flight_type','?')}]: {route}"
        )
        if passengers:
            lines.append(f"  Passengers: {passengers}")
        lines.append(f"  Bags: {res.get('total_baggages', 0)}, insurance: {res.get('insurance', 'no')}")

    return "\n".join(lines)


def _caller_context_retail(user_id: str, users: dict, orders: dict) -> str:
    user = users.get(user_id, {})
    if not user:
        return ""
    name = user["name"]
    addr = user.get("address", {})
    lines = [
        f"Your name: {name['first_name']} {name['last_name']}",
        f"Your email: {user.get('email', '')}",
        f"Your zip code: {addr.get('zip', '')}, state: {addr.get('state', '')}",
    ]
    order_ids = user.get("orders", [])
    if order_ids:
        lines.append(f"Your order IDs: {', '.join(order_ids[:3])}")
    return "\n".join(lines)


def _caller_context_airline(user_id: str, users: dict) -> str:
    user = users.get(user_id, {})
    if not user:
        return ""
    name = user["name"]
    lines = [
        f"Your name: {name['first_name']} {name['last_name']}",
        f"Your email: {user.get('email', '')}",
        f"Your date of birth: {user.get('dob', '')}",
        f"Your membership tier: {user.get('membership', 'none')}",
    ]
    res_ids = user.get("reservations", [])
    if res_ids:
        lines.append(f"Your reservation IDs: {', '.join(res_ids[:3])}")
    return "\n".join(lines)


def task_to_retail_scenario(task: dict, users: dict, orders: dict, idx: int) -> dict | None:
    display = task.get("messages_display", "")
    if not display:
        return None

    turns = _parse_messages(display)
    first_user = _extract_first_user_message(turns)
    if not first_user:
        return None

    user_id = _extract_user_id(turns)
    last_assistant = _extract_last_assistant_message(turns)

    answerer_context = _retail_user_context(user_id, users, orders) if user_id else "(no account data)"
    caller_context = _caller_context_retail(user_id, users, orders) if user_id else ""
    success_phrases = _success_phrases_from_resolution(last_assistant)

    return {
        "id": f"tau_retail_{idx:03d}",
        "description": f"τ-bench retail: {first_user[:60]}",
        "difficulty": "medium",
        "timeout": 120,
        "caller": {
            "goal": (
                f"{first_user}\n\n"
                f"Provide your personal information naturally when the agent asks to verify your identity. "
                f"Confirm the outcome when done."
            ),
            "context": caller_context,
        },
        "answerer": {
            "goal": (
                f"You are a customer service agent for an online retail store.\n"
                f"{_RETAIL_POLICY_SUMMARY}\n\n"
                f"Customer account data:\n{answerer_context}\n\n"
                f"Verify the customer's identity, process their request per policy, "
                f"and confirm the outcome explicitly."
            ),
            "opening_line": "Thank you for calling customer service. How can I help you today?",
        },
        "success_criteria": {
            "goal_phrases": success_phrases,
            "max_turns": 16,
        },
    }


def task_to_airline_scenario(
    task: dict,
    users: dict,
    reservations: dict,
    flights: dict,
    idx: int,
) -> dict | None:
    display = task.get("messages_display", "")
    if not display:
        return None

    turns = _parse_messages(display)
    first_user = _extract_first_user_message(turns)
    if not first_user:
        return None

    user_id = _extract_user_id(turns)
    last_assistant = _extract_last_assistant_message(turns)

    answerer_context = _airline_user_context(user_id, users, reservations, flights) if user_id else "(no account data)"
    caller_context = _caller_context_airline(user_id, users) if user_id else ""
    success_phrases = _success_phrases_from_resolution(last_assistant)

    return {
        "id": f"tau_airline_{idx:03d}",
        "description": f"τ-bench airline: {first_user[:60]}",
        "difficulty": "medium",
        "timeout": 120,
        "caller": {
            "goal": (
                f"{first_user}\n\n"
                f"Provide your personal information naturally when the agent asks. "
                f"Confirm the outcome when done."
            ),
            "context": caller_context,
        },
        "answerer": {
            "goal": (
                f"You are a customer service agent for an airline.\n"
                f"{_AIRLINE_POLICY_SUMMARY}\n\n"
                f"Customer account data:\n{answerer_context}\n\n"
                f"Verify the customer's identity, process their request per policy, "
                f"and confirm the outcome explicitly."
            ),
            "opening_line": "Thank you for calling airline customer service. How may I assist you today?",
        },
        "success_criteria": {
            "goal_phrases": success_phrases,
            "max_turns": 16,
        },
    }


def convert_retail(n: int, output: Path) -> None:
    tasks_path = DATASETS_DIR / "tau_bench" / "retail_tasks.jsonl"
    users_path = DATASETS_DIR / "tau_bench" / "retail_users.json"
    orders_path = DATASETS_DIR / "tau_bench" / "retail_orders.json"

    for p in [tasks_path, users_path, orders_path]:
        if not p.exists():
            print(f"Missing: {p} — run: python datasets/download.py tau_bench", file=sys.stderr)
            sys.exit(1)

    tasks = load_jsonl(tasks_path)
    users = load_json(users_path)
    orders = load_json(orders_path)

    scenarios = []
    for i, task in enumerate(tasks):
        if len(scenarios) >= n:
            break
        scenario = task_to_retail_scenario(task, users, orders, i)
        if scenario:
            scenarios.append(scenario)

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        yaml.dump({"scenarios": scenarios}, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print(f"Wrote {len(scenarios)} retail scenarios → {output}")


def convert_airline(n: int, output: Path) -> None:
    tasks_path = DATASETS_DIR / "tau_bench" / "airline_tasks.jsonl"
    users_path = DATASETS_DIR / "tau_bench" / "airline_users.json"
    res_path = DATASETS_DIR / "tau_bench" / "airline_reservations.json"
    flights_path = DATASETS_DIR / "tau_bench" / "airline_flights.json"

    for p in [tasks_path, users_path, res_path]:
        if not p.exists():
            print(f"Missing: {p} — run: python datasets/download.py tau_bench", file=sys.stderr)
            sys.exit(1)

    tasks = load_jsonl(tasks_path)
    users = load_json(users_path)
    reservations = load_json(res_path)
    flights = load_json(flights_path) if flights_path.exists() else {}

    scenarios = []
    for i, task in enumerate(tasks):
        if len(scenarios) >= n:
            break
        scenario = task_to_airline_scenario(task, users, reservations, flights, i)
        if scenario:
            scenarios.append(scenario)

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        yaml.dump({"scenarios": scenarios}, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print(f"Wrote {len(scenarios)} airline scenarios → {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert τ-bench to voice-agent bench YAML")
    parser.add_argument("--domain", choices=["retail", "airline", "both"], default="both")
    parser.add_argument("--n", type=int, default=20, help="Max scenarios per domain (default: 20)")
    parser.add_argument("--output-retail", type=Path, default=SCENARIOS_DIR / "tau_bench_retail.yaml")
    parser.add_argument("--output-airline", type=Path, default=SCENARIOS_DIR / "tau_bench_airline.yaml")
    args = parser.parse_args()

    if args.domain in ("retail", "both"):
        convert_retail(args.n, args.output_retail)
    if args.domain in ("airline", "both"):
        convert_airline(args.n, args.output_airline)


if __name__ == "__main__":
    main()
