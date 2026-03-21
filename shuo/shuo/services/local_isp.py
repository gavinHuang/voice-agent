"""
LocalISP -- in-process ISP implementation for testing and local-call mode.

Two LocalISP instances are paired via LocalISP.pair(a, b) before starting.
Audio written by one instance is delivered to the other's on_media callback
via an asyncio.Queue, decoding base64 to raw bytes in transit.

Usage:
    a = LocalISP()
    b = LocalISP()
    LocalISP.pair(a, b)
    await a.start(on_media_a, on_start_a, on_stop_a)
    await b.start(on_media_b, on_start_b, on_stop_b)

DTMF injection requires the caller to set _inject on the receiving instance:
    b._inject = some_callable  # called with DTMFToneEvent
"""

import asyncio
import base64
import uuid
from typing import Optional, Callable, Awaitable


class LocalISP:
    """In-process ISP that routes audio between paired instances via asyncio queues."""

    def __init__(self) -> None:
        self._peer: Optional["LocalISP"] = None
        self._on_media: Optional[Callable[[bytes], Awaitable[None]]] = None
        self._on_start: Optional[Callable[[str, str, str], Awaitable[None]]] = None
        self._on_stop: Optional[Callable[[], Awaitable[None]]] = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._inject: Optional[Callable] = None  # Set externally for DTMF event injection

    @classmethod
    def pair(cls, a: "LocalISP", b: "LocalISP") -> None:
        """Connect two LocalISP instances so audio flows between them."""
        a._peer = b
        b._peer = a

    async def start(
        self,
        on_media: Callable[[bytes], Awaitable[None]],
        on_start: Callable[[str, str, str], Awaitable[None]],
        on_stop: Callable[[], Awaitable[None]],
    ) -> None:
        """Register callbacks, spawn reader task, fire on_start immediately."""
        self._on_media = on_media
        self._on_start = on_start
        self._on_stop = on_stop
        self._task = asyncio.create_task(self._reader())
        stream_sid = f"local-{uuid.uuid4().hex[:8]}"
        await on_start(stream_sid, "local-call-sid", "local")

    async def _reader(self) -> None:
        """Background task: drain queue and deliver audio bytes to on_media."""
        while True:
            item = await self._queue.get()
            if item is None:  # None sentinel signals stop
                break
            if self._on_media is not None:
                await self._on_media(item)

    async def send_audio(self, payload: str) -> None:
        """Decode base64 payload and deliver raw bytes to peer's on_media."""
        if self._peer is not None:
            audio_bytes = base64.b64decode(payload)
            await self._peer._queue.put(audio_bytes)

    async def send_clear(self) -> None:
        """No-op: no remote audio buffer exists in an in-process connection."""
        pass

    async def send_dtmf(self, digit: str) -> None:
        """Deliver a DTMF digit to peer via its _inject callable (if set)."""
        if self._peer is not None and self._peer._inject is not None:
            from shuo.types import DTMFToneEvent
            self._peer._inject(DTMFToneEvent(digits=digit))

    async def hangup(self) -> None:
        """Fire peer's on_stop to signal the call has ended."""
        if self._peer is not None and self._peer._on_stop is not None:
            await self._peer._on_stop()

    async def stop(self) -> None:
        """Terminate the background reader task cleanly via sentinel."""
        if self._task is not None:
            await self._queue.put(None)  # sentinel unblocks the reader
            await self._task
            self._task = None

    async def call(self, phone: str, twiml_url: str) -> None:
        """No-op: pairing happens at construction time via pair()."""
        pass
