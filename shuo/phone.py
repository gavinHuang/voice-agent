"""
phone.py — Telephony backends.

Phone is the abstract interface for a call's audio transport.
TwilioPhone: production WebSocket-backed implementation.
LocalPhone:  in-process loopback for local-call mode and benchmarking.

dial_out(): initiate an outbound call via Twilio REST.
parse_twilio_message(): decode raw Twilio WebSocket JSON into typed events.
"""

import os
import json
import base64
import asyncio
import uuid
from typing import Optional, Callable, Awaitable, Protocol

from twilio.rest import Client

from .call import CallStartedEvent, CallEndedEvent, AudioChunkEvent
from .log import Logger, get_logger

logger = get_logger("shuo.phone")


# =============================================================================
# PROTOCOL
# =============================================================================

class Phone(Protocol):
    """
    Pluggable telephony transport for a single call leg.

    start() opens the stream and registers event callbacks.
    stop() tears down the stream cleanly (idempotent).
    send_audio() delivers base64 μ-law audio to the remote party.
    send_clear() flushes the remote audio buffer.
    send_dtmf() injects DTMF digit(s) into the call.
    hangup() terminates the call.
    """

    async def start(
        self,
        on_audio:  Callable[[bytes], Awaitable[None]],
        on_start:  Callable[[str, str, str], Awaitable[None]],  # stream_sid, call_sid, phone
        on_stop:   Callable[[], Awaitable[None]],
    ) -> None: ...

    async def stop(self)                          -> None: ...
    async def send_audio(self, payload: str)      -> None: ...
    async def send_clear(self)                    -> None: ...
    async def send_dtmf(self, digit: str)         -> None: ...
    async def hangup(self)                        -> None: ...
    async def call(self, phone: str, twiml_url: str) -> None: ...


# =============================================================================
# TWILIO PHONE
# =============================================================================

def parse_twilio_message(data: dict):
    """Decode a raw Twilio WebSocket message into a typed event (or None)."""
    event_type = data.get("event")

    if event_type == "connected":
        Logger.websocket_connected()
        return None

    if event_type == "start":
        start     = data.get("start", {})
        stream_sid = start.get("streamSid", "")
        call_sid   = start.get("callSid", "")
        phone      = start.get("customParameters", {}).get("from", "")
        if stream_sid:
            return CallStartedEvent(stream_sid=stream_sid, call_sid=call_sid, phone=phone)

    if event_type == "media":
        payload = data.get("media", {}).get("payload", "")
        if payload:
            return AudioChunkEvent(audio_bytes=base64.b64decode(payload))

    if event_type == "stop":
        return CallEndedEvent()

    return None


class TwilioPhone:
    """Phone implementation backed by a Twilio Media Streams WebSocket."""

    def __init__(self, websocket) -> None:
        self._ws         = websocket
        self._on_audio:  Optional[Callable[[bytes], Awaitable[None]]] = None
        self._on_start:  Optional[Callable[[str, str, str], Awaitable[None]]] = None
        self._on_stop:   Optional[Callable[[], Awaitable[None]]] = None
        self._task:      Optional[asyncio.Task] = None
        self._stream_sid: Optional[str] = None
        self._call_sid:   Optional[str] = None

    async def start(
        self,
        on_audio: Callable[[bytes], Awaitable[None]],
        on_start: Callable[[str, str, str], Awaitable[None]],
        on_stop:  Callable[[], Awaitable[None]],
    ) -> None:
        self._on_audio = on_audio
        self._on_start = on_start
        self._on_stop  = on_stop
        self._task     = asyncio.create_task(self._reader())

    async def _reader(self) -> None:
        try:
            while True:
                raw  = await self._ws.receive_text()
                data = json.loads(raw)
                ev   = parse_twilio_message(data)
                if ev is None:
                    continue
                if isinstance(ev, CallStartedEvent):
                    self._stream_sid = ev.stream_sid
                    self._call_sid   = ev.call_sid
                    await self._on_start(ev.stream_sid, ev.call_sid, ev.phone)
                elif isinstance(ev, AudioChunkEvent):
                    await self._on_audio(ev.audio_bytes)
                elif isinstance(ev, CallEndedEvent):
                    await self._on_stop()
                    break
        except Exception as e:
            logger.error(f"Twilio reader error: {e}")
            if self._on_stop:
                await self._on_stop()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def send_audio(self, payload: str) -> None:
        await self._ws.send_text(json.dumps({
            "event": "media",
            "streamSid": self._stream_sid,
            "media": {"payload": payload},
        }))

    async def send_clear(self) -> None:
        await self._ws.send_text(json.dumps({
            "event": "clear",
            "streamSid": self._stream_sid,
        }))

    async def send_dtmf(self, digit: str) -> None:
        """Redirect call via Twilio REST to play DTMF digit."""
        if not self._call_sid:
            logger.warning("send_dtmf: no call_sid available")
            return
        try:
            loop       = asyncio.get_running_loop()
            client     = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
            public_url = os.getenv("TWILIO_PUBLIC_URL", "")
            dtmf_url   = f"{public_url}/twiml/ivr-dtmf?digit={digit}"
            logger.info(f"DTMF redirect: digit={digit!r} url={dtmf_url}")
            await loop.run_in_executor(
                None, lambda: client.calls(self._call_sid).update(url=dtmf_url, method="POST")
            )
        except Exception as e:
            logger.warning(f"DTMF redirect failed: {e}")

    async def hangup(self) -> None:
        if not self._call_sid:
            return
        try:
            loop   = asyncio.get_running_loop()
            client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
            await loop.run_in_executor(
                None, lambda: client.calls(self._call_sid).update(status="completed")
            )
        except Exception as e:
            logger.warning(f"Hangup failed: {e}")

    async def call(self, phone: str, twiml_url: str) -> None:
        loop = asyncio.get_running_loop()
        self._call_sid = await loop.run_in_executor(None, lambda: dial_out(phone))


# =============================================================================
# LOCAL PHONE  (in-process loopback for local-call and benchmarking)
# =============================================================================

class LocalPhone:
    """
    In-process loopback phone that routes audio between two paired instances.

    Usage:
        a = LocalPhone(); b = LocalPhone()
        LocalPhone.pair(a, b)
        await a.start(...)
        await b.start(...)
    """

    def __init__(self) -> None:
        self._peer:    Optional["LocalPhone"] = None
        self._on_audio: Optional[Callable[[bytes], Awaitable[None]]] = None
        self._on_start: Optional[Callable[[str, str, str], Awaitable[None]]] = None
        self._on_stop:  Optional[Callable[[], Awaitable[None]]] = None
        self._queue:    asyncio.Queue = asyncio.Queue()
        self._task:     Optional[asyncio.Task] = None
        self._inject:   Optional[Callable] = None   # Set by run_call for DTMF injection

    @classmethod
    def pair(cls, a: "LocalPhone", b: "LocalPhone") -> None:
        a._peer = b
        b._peer = a

    async def start(
        self,
        on_audio: Callable[[bytes], Awaitable[None]],
        on_start: Callable[[str, str, str], Awaitable[None]],
        on_stop:  Callable[[], Awaitable[None]],
    ) -> None:
        self._on_audio = on_audio
        self._on_start = on_start
        self._on_stop  = on_stop
        self._task     = asyncio.create_task(self._reader())
        stream_sid     = f"local-{uuid.uuid4().hex[:8]}"
        await on_start(stream_sid, "local-call-sid", "local")

    async def _reader(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            if self._on_audio:
                await self._on_audio(item)

    async def send_audio(self, payload: str) -> None:
        if self._peer:
            await self._peer._queue.put(base64.b64decode(payload))

    async def send_clear(self) -> None:
        pass

    async def send_dtmf(self, digit: str) -> None:
        if self._peer and self._peer._inject:
            from .call import DTMFEvent
            self._peer._inject(DTMFEvent(digits=digit))

    async def hangup(self) -> None:
        if self._peer and self._peer._on_stop:
            await self._peer._on_stop()

    async def stop(self) -> None:
        if self._task:
            await self._queue.put(None)  # sentinel unblocks reader
            await self._task
            self._task = None

    async def call(self, phone: str, twiml_url: str) -> None:
        pass  # pairing happens at construction time via pair()


# =============================================================================
# OUTBOUND CALL
# =============================================================================

def dial_out(to_number: str, ivr_mode: bool = False) -> str:
    """
    Initiate an outbound call via Twilio REST.

    Returns the Twilio call SID.
    ivr_mode=True skips AMD — IVR systems are machines and would be
    incorrectly blocked by answering-machine detection.
    """
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token  = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_PHONE_NUMBER")
    public_url  = os.getenv("TWILIO_PUBLIC_URL")

    if not all([account_sid, auth_token, from_number, public_url]):
        raise ValueError("Missing required Twilio environment variables")

    client   = Client(account_sid, auth_token)
    twiml_url = f"{public_url}/twiml"

    kwargs: dict = dict(to=to_number, from_=from_number, url=twiml_url, record=True)
    if not ivr_mode:
        # async_amd=True: Twilio connects the WebSocket immediately when the
        # callee answers, then runs AMD in the background. Without this,
        # AMD blocks the WebSocket for ~5–7s before our server sees the call.
        kwargs["machine_detection"] = "Enable"
        kwargs["async_amd"] = "true"

    return client.calls.create(**kwargs).sid
