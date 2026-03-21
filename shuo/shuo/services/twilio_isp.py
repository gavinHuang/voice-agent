"""
TwilioISP -- ISP implementation backed by a Twilio WebSocket stream.

Wraps the Twilio Media Streams WebSocket protocol:
- start() spawns a background reader that parses incoming Twilio messages
  and dispatches them via on_media / on_start / on_stop callbacks
- stop() cancels the background reader
- send_audio() / send_clear() format and send Twilio JSON messages
- send_dtmf() / hangup() use the Twilio REST API
- call() initiates an outbound call via make_outbound_call()
"""

import os
import json
import asyncio
from typing import Optional, Callable, Awaitable

from fastapi import WebSocket

from .twilio_client import parse_twilio_message, make_outbound_call
from ..types import StreamStartEvent, StreamStopEvent, MediaEvent
from ..log import get_logger

logger = get_logger("shuo.twilio_isp")


class TwilioISP:
    """ISP implementation backed by a Twilio WebSocket stream."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._on_media: Optional[Callable[[bytes], Awaitable[None]]] = None
        self._on_start: Optional[Callable[[str, str, str], Awaitable[None]]] = None
        self._on_stop: Optional[Callable[[], Awaitable[None]]] = None
        self._task: Optional[asyncio.Task] = None
        self._stream_sid: Optional[str] = None
        self._call_sid: Optional[str] = None

    async def start(
        self,
        on_media: Callable[[bytes], Awaitable[None]],
        on_start: Callable[[str, str, str], Awaitable[None]],
        on_stop: Callable[[], Awaitable[None]],
    ) -> None:
        """Register callbacks and spawn the background WebSocket reader."""
        self._on_media = on_media
        self._on_start = on_start
        self._on_stop = on_stop
        self._task = asyncio.create_task(self._reader())

    async def _reader(self) -> None:
        """Background task: read Twilio WebSocket messages, dispatch via callbacks."""
        try:
            while True:
                raw = await self._websocket.receive_text()
                data = json.loads(raw)
                event = parse_twilio_message(data)
                if event is None:
                    continue
                if isinstance(event, StreamStartEvent):
                    self._stream_sid = event.stream_sid
                    self._call_sid = event.call_sid
                    await self._on_start(event.stream_sid, event.call_sid, event.phone)
                elif isinstance(event, MediaEvent):
                    await self._on_media(event.audio_bytes)
                elif isinstance(event, StreamStopEvent):
                    await self._on_stop()
                    break
        except Exception as e:
            logger.error(f"Twilio reader error: {e}")
            if self._on_stop:
                await self._on_stop()

    async def stop(self) -> None:
        """Cancel the background reader task."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def send_audio(self, payload: str) -> None:
        """Send base64-encoded mu-law audio to Twilio as a media message."""
        message = json.dumps({
            "event": "media",
            "streamSid": self._stream_sid,
            "media": {"payload": payload},
        })
        await self._websocket.send_text(message)

    async def send_clear(self) -> None:
        """Send clear message to Twilio to flush the audio buffer."""
        message = json.dumps({
            "event": "clear",
            "streamSid": self._stream_sid,
        })
        await self._websocket.send_text(message)

    async def send_dtmf(self, digit: str) -> None:
        """Redirect the call via Twilio REST API to play DTMF tone."""
        if not self._call_sid:
            logger.warning("send_dtmf: no call_sid available")
            return
        try:
            from twilio.rest import Client
            loop = asyncio.get_running_loop()
            client = Client(
                os.getenv("TWILIO_ACCOUNT_SID"),
                os.getenv("TWILIO_AUTH_TOKEN"),
            )
            public_url = os.getenv("TWILIO_PUBLIC_URL", "")
            dtmf_url = f"{public_url}/twiml/ivr-dtmf?digit={digit}"
            logger.info(f"DTMF redirect: digit={digit!r} url={dtmf_url}")
            await loop.run_in_executor(
                None, lambda: client.calls(self._call_sid).update(url=dtmf_url, method="POST")
            )
        except Exception as e:
            logger.warning(f"DTMF redirect failed: {e}")

    async def hangup(self) -> None:
        """Hang up the call via Twilio REST API."""
        if not self._call_sid:
            return
        try:
            from twilio.rest import Client
            loop = asyncio.get_running_loop()
            client = Client(
                os.getenv("TWILIO_ACCOUNT_SID"),
                os.getenv("TWILIO_AUTH_TOKEN"),
            )
            await loop.run_in_executor(
                None, lambda: client.calls(self._call_sid).update(status="completed")
            )
        except Exception as e:
            logger.warning(f"Hangup REST call failed: {e}")

    async def call(self, phone: str, twiml_url: str) -> None:
        """Initiate an outbound call via Twilio REST API."""
        loop = asyncio.get_running_loop()
        call_sid = await loop.run_in_executor(None, lambda: make_outbound_call(phone))
        self._call_sid = call_sid
