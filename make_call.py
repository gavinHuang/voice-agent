#!/usr/bin/env python3
"""Simple script to place a phone call using Twilio."""

import logging
import os
import sys

from dotenv import load_dotenv
from twilio.rest import Client

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# Load config from shuo/.env
load_dotenv(os.path.join(os.path.dirname(__file__), "shuo", ".env"))

ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
FROM_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <phone_number>")
        sys.exit(1)

    TO_NUMBER = sys.argv[1]

    if not all([ACCOUNT_SID, AUTH_TOKEN, FROM_NUMBER]):
        log.error("Missing Twilio credentials in .env")
        sys.exit(1)

    log.info("Twilio Account SID: %s", ACCOUNT_SID)
    log.info("From number: %s", FROM_NUMBER)
    log.info("To number: %s", TO_NUMBER)

    client = Client(ACCOUNT_SID, AUTH_TOKEN)
    log.debug("Twilio client created")

    try:
        call = client.calls.create(
            to=TO_NUMBER,
            from_=FROM_NUMBER,
            twiml="<Response><Say>Hello, this is a test call from the voice agent system. Goodbye.</Say></Response>",
        )
        log.info("Call initiated — SID: %s", call.sid)
        log.info("Call status: %s", call.status)
    except Exception:
        log.exception("Failed to create call")
        sys.exit(1)


if __name__ == "__main__":
    main()
