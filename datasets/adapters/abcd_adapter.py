#!/usr/bin/env python3
"""Convert ABCD (Action-Based Conversations Dataset) to voice-agent two-agent bench YAML.

Downloads required if not present; reads from datasets/abcd/.
Produces scenarios usable with:
    voice-agent bench --mode two-agent --dataset scenarios/abcd_bench.yaml

Usage:
    python datasets/adapters/abcd_adapter.py [--n 20] [--split train|dev|test|sample]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

DATASETS_DIR = Path(__file__).parent.parent
SCENARIOS_DIR = DATASETS_DIR.parent / "scenarios"

# Map ABCD flow → caller goal template
_FLOW_GOALS: dict[str, str] = {
    "account_access": (
        "You need help accessing your account. "
        "You have forgotten your {issue} and need help recovering it."
    ),
    "manage_account": (
        "You need to update your account information. "
        "You want to {action} on your account."
    ),
    "order_issue": (
        "You are calling about an issue with your order #{order_id}. "
        "The problem is: {subflow_desc}."
    ),
    "product_defect": (
        "You received a defective product from order #{order_id} "
        "and need to {subflow_desc}."
    ),
    "purchase_dispute": (
        "You are disputing a charge on your account related to order #{order_id}. "
        "You need to {subflow_desc}."
    ),
    "shipping_issue": (
        "There is a problem with the shipping of your order #{order_id}. "
        "You need to {subflow_desc}."
    ),
    "single_item_query": (
        "You have a question about a specific item in your order #{order_id}. "
        "You want to know {subflow_desc}."
    ),
    "storewide_query": (
        "You have a general question about the store. "
        "You want to know {subflow_desc}."
    ),
    "subscription_inquiry": (
        "You have a question about your subscription. "
        "You want to {subflow_desc}."
    ),
    "troubleshoot_site": (
        "You are having trouble with the website or app. "
        "The problem is: {subflow_desc}."
    ),
}

# Subflow descriptions (human-readable)
_SUBFLOW_DESC: dict[str, str] = {
    "return_size": "return an item that doesn't fit",
    "return_damaged": "return a damaged item",
    "return_missing_parts": "return an item with missing parts",
    "wrong_item": "report that you received the wrong item",
    "late_delivery": "report a late delivery",
    "status": "check the status of your order",
    "missing_order": "report that your order is missing",
    "storewide_sale": "ask about current sales",
    "product_availability": "check product availability",
    "recover_username": "recover your username",
    "recover_password": "reset your password",
    "update_email": "update your email address",
    "update_address": "update your shipping address",
    "update_payment": "update your payment method",
}

# Difficulty based on complexity of flow
_FLOW_DIFFICULTY: dict[str, str] = {
    "account_access": "easy",
    "manage_account": "easy",
    "single_item_query": "easy",
    "storewide_query": "easy",
    "order_issue": "medium",
    "shipping_issue": "medium",
    "subscription_inquiry": "medium",
    "troubleshoot_site": "medium",
    "product_defect": "hard",
    "purchase_dispute": "hard",
}

# Expected success phrases by flow
_FLOW_SUCCESS_PHRASES: dict[str, list[str]] = {
    "account_access": ["account found", "verified", "username", "password"],
    "manage_account": ["updated", "changed", "saved"],
    "order_issue": ["resolved", "refund", "replacement", "return"],
    "product_defect": ["return", "replacement", "refund"],
    "purchase_dispute": ["dispute", "investigation", "refund"],
    "shipping_issue": ["tracking", "reshipped", "refund"],
    "single_item_query": ["here is", "the item", "product"],
    "storewide_query": ["sale", "discount", "available"],
    "subscription_inquiry": ["subscription", "plan", "renewal"],
    "troubleshoot_site": ["resolved", "try", "cleared"],
}


def _load_abcd_data(split: str) -> list[dict]:
    """Load ABCD data from the downloaded files."""
    full_path = DATASETS_DIR / "abcd" / "abcd_v1.1.json"
    sample_path = DATASETS_DIR / "abcd" / "abcd_sample.json"

    if full_path.exists():
        with open(full_path) as f:
            data = json.load(f)
        # Full dataset is {"train": [...], "dev": [...], "test": [...]}
        if isinstance(data, dict):
            return data.get(split, data.get("train", []))
        return data if isinstance(data, list) else []

    if sample_path.exists():
        with open(sample_path) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []

    return []


def _load_guidelines() -> dict:
    path = DATASETS_DIR / "abcd" / "guidelines.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def _subflow_description(subflow: str) -> str:
    return _SUBFLOW_DESC.get(subflow, subflow.replace("_", " "))


def _extract_first_customer_utterance(turns: list[dict]) -> str:
    for turn in turns:
        if turn.get("speaker") in ("customer", "user"):
            return turn.get("text", "")
    return ""


def dialogue_to_scenario(
    dialogue: dict,
    guidelines: dict,
    idx: int,
) -> dict | None:
    scenario_meta = dialogue.get("scenario", {})
    personal = scenario_meta.get("personal", {})
    order = scenario_meta.get("order", {})
    flow = scenario_meta.get("flow", "order_issue")
    subflow = scenario_meta.get("subflow", "status")

    # original is a list of [speaker, text] pairs
    raw_turns = dialogue.get("original", [])
    conv_turns = [t for t in raw_turns if isinstance(t, list) and len(t) == 2
                  and t[0] in ("customer", "agent")]
    if not conv_turns:
        return None

    order_id = str(order.get("order_id", "")) or "unknown"
    subflow_desc = _subflow_description(subflow)
    name = personal.get("customer_name", "Customer")
    email = personal.get("email", "")
    membership = personal.get("member_level", "bronze")
    phone = personal.get("phone", "")
    username = personal.get("username", "")

    # Build caller goal from flow template
    goal_template = _FLOW_GOALS.get(flow, "You need help with your order #{order_id}.")
    caller_goal = goal_template.format(
        order_id=order_id,
        subflow_desc=subflow_desc,
        issue="password" if "password" in subflow else "username",
        action=subflow_desc,
    )
    # Add instruction to provide info naturally when asked
    caller_goal += (
        f"\n\nProvide your information naturally when the agent asks. "
        f"Your goal is to successfully {subflow_desc}."
    )

    caller_context = (
        f"Your name: {name}\n"
        f"Your email: {email}\n"
        f"Your phone: {phone}\n"
        f"Your username: {username}\n"
        f"Your membership: {membership}\n"
        f"Your order ID: {order_id}"
    )

    # Build items description for agent
    # products field is a string representation of a list in ABCD
    products_raw = order.get("products", "")
    try:
        import ast
        products = ast.literal_eval(products_raw) if isinstance(products_raw, str) else products_raw
    except Exception:
        products = []
    items_str = ", ".join(
        f"{p.get('product_type', 'item')} by {p.get('brand', '')} (${p.get('amount', 0)})"
        for p in products[:3]
    ) if products else "unknown items"
    purchase_date = order.get("purchase_date", "recently")
    address = order.get("full_address", order.get("street_address", "on file"))

    # Load guidelines for this flow if available
    flow_guidelines = ""
    if guidelines and flow in guidelines:
        flow_data = guidelines[flow]
        subflows = flow_data.get("subflows", {})
        if subflow in subflows:
            steps = subflows[subflow]
            step_texts = [s.get("text", "") for s in steps[:3] if s.get("type") == "interaction"]
            if step_texts:
                flow_guidelines = "Steps to follow: " + "; ".join(step_texts[:3])

    answerer_goal = (
        f"You are a customer service agent for an e-commerce company.\n"
        f"You must follow company guidelines for every interaction.\n"
        f"{flow_guidelines}\n\n" if flow_guidelines else
        f"You are a customer service agent for an e-commerce company.\n\n"
    ) + (
        f"Customer account:\n"
        f"  Name: {name}\n"
        f"  Email: {email}\n"
        f"  Phone: {phone}\n"
        f"  Username: {username}\n"
        f"  Membership: {membership}\n"
        f"  Order #{order_id}: {items_str} purchased {purchase_date}, ship to {address}\n\n"
        f"Verify the customer's identity, then resolve their issue following company policy. "
        f"Confirm the resolution at the end."
    )

    success_phrases = _FLOW_SUCCESS_PHRASES.get(flow, ["resolved", "taken care of"])

    return {
        "id": f"abcd_{flow}_{idx:03d}",
        "description": f"ABCD {flow}/{subflow}",
        "difficulty": _FLOW_DIFFICULTY.get(flow, "medium"),
        "timeout": 120,
        "caller": {
            "goal": caller_goal,
            "context": caller_context,
        },
        "answerer": {
            "goal": answerer_goal,
            "opening_line": "Thank you for calling customer support. How can I help you today?",
        },
        "success_criteria": {
            "goal_phrases": success_phrases[:2],
            "max_turns": 16,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert ABCD to voice-agent bench YAML")
    parser.add_argument(
        "--split",
        choices=["train", "dev", "test", "sample"],
        default="sample",
        help="Which data split to use (default: sample)",
    )
    parser.add_argument("--n", type=int, default=20, help="Max scenarios to convert (default: 20)")
    parser.add_argument(
        "--output",
        type=Path,
        default=SCENARIOS_DIR / "abcd_bench.yaml",
    )
    args = parser.parse_args()

    abcd_full = DATASETS_DIR / "abcd" / "abcd_v1.1.json"
    abcd_sample = DATASETS_DIR / "abcd" / "abcd_sample.json"
    if not abcd_full.exists() and not abcd_sample.exists():
        print("Missing ABCD data — run: python datasets/download.py abcd", file=sys.stderr)
        sys.exit(1)

    dialogues = _load_abcd_data(args.split)
    if not dialogues:
        print(f"No dialogues found in split '{args.split}'", file=sys.stderr)
        sys.exit(1)

    guidelines = _load_guidelines()

    # Sample diverse flows
    seen_flows: dict[str, int] = {}
    scenarios: list[dict] = []
    for i, dialogue in enumerate(dialogues):
        if len(scenarios) >= args.n:
            break
        flow = dialogue.get("scenario", {}).get("flow", "")
        # Allow up to 3 per flow to ensure diversity
        if seen_flows.get(flow, 0) >= 3:
            continue
        scenario = dialogue_to_scenario(dialogue, guidelines, i)
        if scenario:
            scenarios.append(scenario)
            seen_flows[flow] = seen_flows.get(flow, 0) + 1

    # If we haven't hit n yet, fill without flow diversity restriction
    if len(scenarios) < args.n:
        for i, dialogue in enumerate(dialogues):
            if len(scenarios) >= args.n:
                break
            scenario = dialogue_to_scenario(dialogue, guidelines, i)
            if scenario and not any(s["id"] == scenario["id"] for s in scenarios):
                scenarios.append(scenario)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        yaml.dump(
            {"scenarios": scenarios},
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
    print(f"Wrote {len(scenarios)} scenarios → {args.output}")
    flow_counts = {}
    for s in scenarios:
        flow = s["id"].split("_")[1]
        flow_counts[flow] = flow_counts.get(flow, 0) + 1
    for flow, count in sorted(flow_counts.items()):
        print(f"  {flow}: {count}")


if __name__ == "__main__":
    main()
