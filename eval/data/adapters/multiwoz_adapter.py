#!/usr/bin/env python3
"""Convert MultiWOZ 2.2 dialogues to voice-agent two-agent bench YAML.

Downloads required if not present; reads from datasets/multiwoz/.
Produces scenarios usable with:
    voice-agent bench --mode two-agent --dataset scenarios/multiwoz_bench.yaml

Usage:
    python datasets/adapters/multiwoz_adapter.py [--n 20]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

DATASETS_DIR = Path(__file__).parent.parent
SCENARIOS_DIR = DATASETS_DIR.parent / "scenarios"

# Which test dialogue files to load (in order)
_DIALOGUE_FILES = [
    "test_dialogues_001.json",
    "test_dialogues_002.json",
]

# Domain-specific agent descriptions
_DOMAIN_AGENT_ROLE: dict[str, str] = {
    "hotel": "a hotel booking assistant for Cambridge, UK",
    "restaurant": "a restaurant reservation assistant for Cambridge, UK",
    "train": "a train booking assistant for UK rail services",
    "taxi": "a taxi booking assistant for local cab service",
    "attraction": "a tourist information assistant for Cambridge, UK",
    "hospital": "a hospital information assistant",
    "police": "a police department information assistant",
    "bus": "a bus information assistant",
}

_DOMAIN_OPENING: dict[str, str] = {
    "hotel": "Cambridge Tourist Information, hotel reservations. How can I help you?",
    "restaurant": "Cambridge Tourist Information, restaurant bookings. How can I help?",
    "train": "UK Rail enquiries. How can I assist you today?",
    "taxi": "Cambridge Taxis. Where would you like to go?",
    "attraction": "Cambridge Tourist Information. How can I help you?",
    "hospital": "Cambridge Hospital information desk. How can I help?",
    "police": "Cambridge Police. How can I assist you?",
    "bus": "Cambridge Bus information. How can I help?",
}

# Slot → human-readable name
_SLOT_LABELS: dict[str, str] = {
    "hotel-name": "hotel name",
    "hotel-area": "area",
    "hotel-pricerange": "price range",
    "hotel-type": "type",
    "hotel-parking": "parking",
    "hotel-internet": "internet",
    "hotel-stars": "star rating",
    "hotel-day": "check-in day",
    "hotel-people": "number of guests",
    "hotel-stay": "nights",
    "restaurant-name": "restaurant name",
    "restaurant-area": "area",
    "restaurant-pricerange": "price range",
    "restaurant-food": "cuisine",
    "restaurant-day": "day",
    "restaurant-people": "number of people",
    "restaurant-time": "time",
    "train-departure": "departure station",
    "train-destination": "destination station",
    "train-day": "travel day",
    "train-arriveby": "arrival time",
    "train-leaveat": "departure time",
    "train-people": "number of tickets",
    "taxi-departure": "pickup location",
    "taxi-destination": "drop-off location",
    "taxi-arriveby": "arrival time",
    "taxi-leaveat": "departure time",
    "attraction-name": "attraction name",
    "attraction-area": "area",
    "attraction-type": "type",
}

# Success phrases by domain
_DOMAIN_SUCCESS: dict[str, list[str]] = {
    "hotel": ["booked", "reservation", "reference number"],
    "restaurant": ["booked", "reservation", "reference number"],
    "train": ["booked", "ticket", "reference number", "train ID"],
    "taxi": ["booked", "taxi", "cab"],
    "attraction": ["located", "here is", "address", "phone"],
    "hospital": ["address", "phone", "here is"],
    "police": ["address", "phone", "here is"],
}

# Requestable → expected info phrase
_REQUESTABLE_PHRASES: dict[str, str] = {
    "phone": "phone number",
    "address": "address",
    "postcode": "postcode",
    "reference": "reference number",
    "trainID": "train",
    "price": "price",
    "duration": "duration",
    "id": "id",
}


def _load_dialogues(files: list[str]) -> list[dict]:
    all_dialogues = []
    for filename in files:
        path = DATASETS_DIR / "multiwoz" / filename
        if not path.exists():
            continue
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, list):
            all_dialogues.extend(data)
        elif isinstance(data, dict):
            # MultiWOZ 2.2 format: {"dialogue_id": {...}, ...}
            all_dialogues.extend(data.values())
    return all_dialogues


def _extract_goal_from_turns(turns: list[dict]) -> tuple[str, list[str]]:
    """Extract user intent from first few turns. Returns (goal_text, services)."""
    user_utterances = []
    services = set()
    for turn in turns[:6]:
        if turn.get("speaker", "").lower() in ("user",):
            utt = turn.get("utterance", "").strip()
            if utt:
                user_utterances.append(utt)
        # Extract active services from frames
        for frame in turn.get("frames", []):
            svc = frame.get("service", "")
            if svc:
                services.add(svc)
    return " ".join(user_utterances[:2]), list(services)


def _extract_goal_slots(turns: list[dict]) -> dict[str, dict[str, list[str]]]:
    """Extract slot constraints from dialogue state across all turns."""
    goal_slots: dict[str, dict[str, list[str]]] = {}
    for turn in turns:
        for frame in turn.get("frames", []):
            svc = frame.get("service", "")
            if not svc:
                continue
            state = frame.get("state", {})
            slot_values = state.get("slot_values", {})
            if slot_values:
                if svc not in goal_slots:
                    goal_slots[svc] = {}
                for slot, values in slot_values.items():
                    # Keep most constrained (last seen) values
                    goal_slots[svc][slot] = values
    return goal_slots


def _extract_requestables(turns: list[dict]) -> dict[str, list[str]]:
    """Extract what information the user is requesting (requestable slots)."""
    requestables: dict[str, list[str]] = {}
    for turn in turns:
        if turn.get("speaker", "").lower() != "user":
            continue
        for frame in turn.get("frames", []):
            svc = frame.get("service", "")
            acts = frame.get("actions", [])
            for act in acts:
                if act.get("act") == "REQUEST":
                    slot = act.get("slot", "")
                    if slot:
                        if svc not in requestables:
                            requestables[svc] = []
                        if slot not in requestables[svc]:
                            requestables[svc].append(slot)
    return requestables


def _build_caller_goal(
    slot_values: dict[str, dict[str, list[str]]],
    requestables: dict[str, list[str]],
    first_utterances: str,
) -> str:
    if not slot_values:
        return first_utterances or "I need some help finding information."

    domain_goals = []
    for svc, slots in slot_values.items():
        constraints = []
        for slot_key, values in slots.items():
            if values and values[0] not in ("dontcare", "not mentioned", ""):
                label = _SLOT_LABELS.get(slot_key, slot_key.split("-", 1)[-1])
                constraints.append(f"{label}: {values[0]}")

        reqs = requestables.get(svc, [])
        req_labels = [_SLOT_LABELS.get(f"{svc}-{r}", r) for r in reqs]

        goal = f"Find a {svc}"
        if constraints:
            goal += f" with {', '.join(constraints)}"
        if req_labels:
            goal += f". You want to know the {', '.join(req_labels)}"
        if svc in ("hotel", "restaurant", "train"):
            goal += ". Book it if suitable."
        domain_goals.append(goal)

    return ". ".join(domain_goals) + "."


def _build_answerer_goal(
    services: list[str],
    slot_values: dict[str, dict[str, list[str]]],
) -> str:
    if not services:
        services = ["attraction"]

    roles = [_DOMAIN_AGENT_ROLE.get(svc, f"a {svc} assistant") for svc in services]
    role_str = " and ".join(roles[:2])

    lines = [
        f"You are {role_str}.",
        "You have access to a database of local options and can make bookings.",
        "Help the customer find what they are looking for and provide accurate information.",
        "If they want to book, confirm with a reference number.",
        "Be concise and helpful.",
    ]

    # Add domain context hints
    for svc, slots in slot_values.items():
        constraints = []
        for slot_key, values in slots.items():
            if values and values[0] not in ("dontcare", "not mentioned", ""):
                label = _SLOT_LABELS.get(slot_key, slot_key.split("-", 1)[-1])
                constraints.append(f"{label}={values[0]}")
        if constraints:
            lines.append(f"Customer needs ({svc}): {', '.join(constraints)}")

    return "\n".join(lines)


def _primary_service(services: list[str]) -> str:
    priority = ["hotel", "train", "restaurant", "taxi", "attraction", "hospital", "police", "bus"]
    for p in priority:
        if p in services:
            return p
    return services[0] if services else "attraction"


def dialogue_to_scenario(dialogue: dict, idx: int) -> dict | None:
    turns = dialogue.get("turns", [])
    if not turns:
        return None

    # Get services for this dialogue
    services_raw = dialogue.get("services", [])
    if not services_raw:
        services_raw = []

    first_utterances, turn_services = _extract_goal_from_turns(turns)
    services = services_raw or turn_services
    if not services:
        return None

    slot_values = _extract_goal_slots(turns)
    requestables = _extract_requestables(turns)
    caller_goal = _build_caller_goal(slot_values, requestables, first_utterances)
    answerer_goal = _build_answerer_goal(services, slot_values)

    primary = _primary_service(services)
    opening = _DOMAIN_OPENING.get(primary, "Cambridge information desk. How can I help?")

    success_phrases = _DOMAIN_SUCCESS.get(primary, ["here is", "found"])
    # Also add requestable phrase hints
    for svc, reqs in requestables.items():
        for r in reqs[:1]:
            p = _REQUESTABLE_PHRASES.get(r, r)
            if p not in success_phrases:
                success_phrases.append(p)
    success_phrases = success_phrases[:3]

    domain_str = "+".join(services[:2])
    dialogue_id = dialogue.get("dialogue_id", str(idx))

    return {
        "id": f"multiwoz_{domain_str}_{idx:03d}",
        "description": f"MultiWOZ {domain_str} booking/info task ({dialogue_id})",
        "difficulty": "medium" if len(services) > 1 else "easy",
        "timeout": 120,
        "caller": {
            "goal": caller_goal,
        },
        "answerer": {
            "goal": answerer_goal,
            "opening_line": opening,
        },
        "success_criteria": {
            "goal_phrases": success_phrases,
            "max_turns": 20,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert MultiWOZ to voice-agent bench YAML")
    parser.add_argument("--n", type=int, default=20, help="Max scenarios to convert (default: 20)")
    parser.add_argument(
        "--output",
        type=Path,
        default=SCENARIOS_DIR / "multiwoz_bench.yaml",
    )
    args = parser.parse_args()

    missing = [f for f in _DIALOGUE_FILES if not (DATASETS_DIR / "multiwoz" / f).exists()]
    if missing:
        print(f"Missing files: {missing} — run: python datasets/download.py multiwoz", file=sys.stderr)
        sys.exit(1)

    dialogues = _load_dialogues(_DIALOGUE_FILES)
    if not dialogues:
        print("No dialogues loaded — check datasets/multiwoz/", file=sys.stderr)
        sys.exit(1)

    # Sample with domain diversity
    seen_domains: dict[str, int] = {}
    scenarios: list[dict] = []
    for i, dialogue in enumerate(dialogues):
        if len(scenarios) >= args.n:
            break
        services = dialogue.get("services", [])
        primary = _primary_service(services) if services else "unknown"
        if seen_domains.get(primary, 0) >= 4:
            continue
        scenario = dialogue_to_scenario(dialogue, i)
        if scenario:
            scenarios.append(scenario)
            seen_domains[primary] = seen_domains.get(primary, 0) + 1

    # Fill remaining without diversity restriction
    for i, dialogue in enumerate(dialogues):
        if len(scenarios) >= args.n:
            break
        scenario = dialogue_to_scenario(dialogue, i)
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
    domain_counts: dict[str, int] = {}
    for s in scenarios:
        parts = s["id"].split("_")
        domain = parts[1] if len(parts) > 1 else "unknown"
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
    for domain, count in sorted(domain_counts.items()):
        print(f"  {domain}: {count}")


if __name__ == "__main__":
    main()
