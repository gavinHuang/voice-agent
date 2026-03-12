#!/usr/bin/env python3
"""List all ongoing Twilio calls and hang them up to prevent ghost calls."""

import os
import sys

from dotenv import load_dotenv
from twilio.rest import Client

# Load config from shuo/.env
load_dotenv(os.path.join(os.path.dirname(__file__), "shuo", ".env"))

ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

ACTIVE_STATUSES = ["queued", "ringing", "in-progress"]


def main():
    if not all([ACCOUNT_SID, AUTH_TOKEN]):
        print("Missing TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN in .env")
        sys.exit(1)

    client = Client(ACCOUNT_SID, AUTH_TOKEN)

    # Collect all active calls
    active_calls = []
    for status in ACTIVE_STATUSES:
        calls = client.calls.list(status=status)
        active_calls.extend(calls)

    if not active_calls:
        print("No active calls found.")
        return

    print(f"Found {len(active_calls)} active call(s):\n")
    print(f"{'SID':<40} {'Status':<15} {'From':<18} {'To':<18} {'Direction':<12} {'Duration'}")
    print("-" * 120)

    for call in active_calls:
        print(f"{call.sid:<40} {call.status:<15} {call.from_formatted:<18} {call.to_formatted:<18} {call.direction:<12} {call.duration or '-'}")

    if "--list" in sys.argv:
        return

    print(f"\nHanging up all {len(active_calls)} call(s)...")

    for call in active_calls:
        try:
            call.update(status="completed")
            print(f"  Hung up {call.sid}")
        except Exception as e:
            print(f"  Failed to hang up {call.sid}: {e}")

    print("Done.")


if __name__ == "__main__":
    main()
