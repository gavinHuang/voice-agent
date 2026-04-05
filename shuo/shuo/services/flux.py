"""
Deepgram Flux service -- always-on STT + turn detection.

A single persistent WebSocket to Deepgram using the v2 listen API.
Receives all Twilio audio continuously and emits turn events.

Replaces both local VAD (Silero) and separate STT (Deepgram v1).
"""

import os
import asyncio
from typing import Optional, Callable, Awaitable

from deepgram import AsyncDeepgramClient, DeepgramClientEnvironment

from ..log import ServiceLogger

log = ServiceLogger("Flux")

_MAX_RECONNECTS = int(os.getenv("FLUX_MAX_RECONNECTS", "3"))
_RECONNECT_BASE_DELAY = float(os.getenv("FLUX_RECONNECT_BASE_DELAY", "1.0"))


class FluxService:
    """
    Deepgram Flux streaming service.

    Audio format: mulaw 8kHz (direct from Twilio, no conversion needed).
    Turn events: StartOfTurn (barge-in), EndOfTurn (with transcript).

    Reconnects automatically on unexpected disconnect (up to _MAX_RECONNECTS
    times with exponential backoff). If all reconnects fail, calls on_dead().
    """

    def __init__(
        self,
        on_end_of_turn: Callable[[str], Awaitable[None]],
        on_start_of_turn: Callable[[], Awaitable[None]],
        on_interim: Optional[Callable[[str], Awaitable[None]]] = None,
        on_dead: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self._on_end_of_turn = on_end_of_turn
        self._on_start_of_turn = on_start_of_turn
        self._on_interim = on_interim
        self._on_dead = on_dead

        self._api_key = os.getenv("DEEPGRAM_API_KEY", "")
        self._client: Optional[AsyncDeepgramClient] = None
        self._connection = None
        self._cm = None
        self._listener_task: Optional[asyncio.Task] = None
        self._running = False
        self._reconnect_count = 0
        self._reconnect_task: Optional[asyncio.Task] = None

    @property
    def is_active(self) -> bool:
        return self._running and self._connection is not None

    def bind(
        self,
        on_end_of_turn: Callable[[str], Awaitable[None]],
        on_start_of_turn: Callable[[], Awaitable[None]],
        on_interim: Optional[Callable[[str], Awaitable[None]]] = None,
        on_dead: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        """Rebind callbacks — used by FluxPool when dispensing a warm connection."""
        self._on_end_of_turn = on_end_of_turn
        self._on_start_of_turn = on_start_of_turn
        self._on_interim = on_interim
        self._on_dead = on_dead

    async def start(self) -> None:
        """Connect to Deepgram Flux (always-on for the duration of the call)."""
        if self._running:
            return

        try:
            deepgram_eu = DeepgramClientEnvironment(
                base="wss://api.eu.deepgram.com",
                production="wss://api.eu.deepgram.com",
                agent="wss://agent.eu.deepgram.com",
            )
            self._client = AsyncDeepgramClient(
                api_key=self._api_key,
                environment=deepgram_eu,
            )

            self._cm = self._client.listen.v2.connect(
                model="flux-general-en",
                encoding="mulaw",
                sample_rate=8000,
            )
            self._connection = await self._cm.__aenter__()

            self._connection.on("message", self._on_message)
            self._connection.on("error", self._on_error)
            self._connection.on("close", self._on_close)

            self._listener_task = asyncio.create_task(
                self._connection.start_listening()
            )

            self._running = True
            log.connected()

        except Exception as e:
            log.error("Connection failed", e)
            await self._cleanup()
            raise

    async def send(self, audio_bytes: bytes) -> None:
        """Send audio chunk to Deepgram Flux."""
        if not self._connection or not self._running:
            return

        try:
            await asyncio.wait_for(self._connection.send_media(audio_bytes), timeout=0.1)
        except Exception as e:
            log.error("Send failed", e)
            # Stop future sends — connection is dead, don't block the event loop
            self._running = False
            self._connection = None

    async def stop(self) -> None:
        """Disconnect from Deepgram Flux."""
        self._running = False
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None
        await self._cleanup()
        log.disconnected()

    async def _cleanup(self) -> None:
        """Clean up resources."""
        self._running = False

        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

        if self._cm:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._cm = None

        self._connection = None
        self._client = None

    async def _on_message(self, message, *args, **kwargs) -> None:
        """Handle Flux messages -- parse TurnInfo events."""
        try:
            msg_type = getattr(message, "type", None)

            if msg_type == "TurnInfo":
                event = getattr(message, "event", None)

                if event == "EndOfTurn":
                    transcript = getattr(message, "transcript", "") or ""
                    await self._on_end_of_turn(transcript.strip())

                elif event == "StartOfTurn":
                    await self._on_start_of_turn()

            elif msg_type == "Results" and self._on_interim:
                channel = getattr(message, "channel", None)
                if channel:
                    alternatives = getattr(channel, "alternatives", None)
                    if alternatives:
                        alt = (
                            alternatives[0]
                            if isinstance(alternatives, list)
                            else alternatives
                        )
                        transcript = getattr(alt, "transcript", "")
                        if transcript:
                            await self._on_interim(transcript.strip())

        except Exception as e:
            log.error("Message handling failed", e)

    async def _on_close(self, *args, **kwargs) -> None:
        """Handle Deepgram connection close — attempt reconnect."""
        if self._running:
            log.warning("Deepgram connection closed unexpectedly")
            self._connection = None
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _on_error(self, error, *args, **kwargs) -> None:
        """Handle Deepgram errors — attempt reconnect."""
        log.error("Deepgram error: " + str(error))
        self._connection = None
        if self._running:
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Reconnect with exponential backoff. Calls on_dead() if all attempts fail."""
        while self._running and self._reconnect_count < _MAX_RECONNECTS:
            delay = _RECONNECT_BASE_DELAY * (2 ** self._reconnect_count)
            self._reconnect_count += 1
            log.warning(f"Reconnecting to Deepgram (attempt {self._reconnect_count}/{_MAX_RECONNECTS}) in {delay:.1f}s")
            await asyncio.sleep(delay)

            if not self._running:
                return

            try:
                await self._cleanup()
                deepgram_eu = DeepgramClientEnvironment(
                    base="wss://api.eu.deepgram.com",
                    production="wss://api.eu.deepgram.com",
                    agent="wss://agent.eu.deepgram.com",
                )
                self._client = AsyncDeepgramClient(
                    api_key=self._api_key,
                    environment=deepgram_eu,
                )
                self._cm = self._client.listen.v2.connect(
                    model="flux-general-en",
                    encoding="mulaw",
                    sample_rate=8000,
                )
                self._connection = await self._cm.__aenter__()
                self._connection.on("message", self._on_message)
                self._connection.on("error", self._on_error)
                self._connection.on("close", self._on_close)
                self._listener_task = asyncio.create_task(
                    self._connection.start_listening()
                )
                self._reconnect_count = 0  # reset on success
                log.connected()
                log.info("Deepgram reconnected successfully")
                return
            except Exception as e:
                log.error(f"Reconnect attempt {self._reconnect_count} failed", e)

        # All reconnects exhausted
        if self._running:
            log.error(f"Deepgram permanently unavailable after {_MAX_RECONNECTS} attempts — hanging up")
            self._running = False
            if self._on_dead:
                await self._on_dead()
