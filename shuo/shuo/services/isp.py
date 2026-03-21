"""
ISP (In-Session Protocol) -- abstract interface for telephony backends.

Defines the contract that all ISP implementations must satisfy.
Structural typing via Python Protocol -- no explicit inheritance needed.

Implementations: TwilioISP (production), LocalISP (testing/local-call mode)
"""

from typing import Protocol, Callable, Awaitable


class ISP(Protocol):
    """
    Interface for an in-session telephony provider.

    An ISP manages the audio stream for a single call leg:
    - start() opens the stream and registers callbacks
    - stop() tears down the stream cleanly
    - send_audio() delivers base64-encoded mu-law audio to the remote party
    - send_clear() flushes any remote audio buffer
    - send_dtmf() injects DTMF tone digits into the call
    - hangup() terminates the call
    - call() initiates an outbound call (where supported)
    """

    async def start(
        self,
        on_media: Callable[[bytes], Awaitable[None]],
        on_start: Callable[[str, str, str], Awaitable[None]],  # stream_sid, call_sid, phone
        on_stop: Callable[[], Awaitable[None]],
    ) -> None: ...

    async def stop(self) -> None: ...

    async def send_audio(self, payload: str) -> None: ...

    async def send_clear(self) -> None: ...

    async def send_dtmf(self, digit: str) -> None: ...

    async def hangup(self) -> None: ...

    async def call(self, phone: str, twiml_url: str) -> None: ...
