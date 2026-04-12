"""
speech.py — Real-time speech-to-text and turn detection via Deepgram Flux.

Transcriber wraps a persistent Deepgram WebSocket that receives μ-law audio
and fires callbacks for turn boundaries (start/end-of-turn).

TranscriberPool pre-warms connections to eliminate cold-start latency.

Audio format: mulaw 8kHz (direct from Twilio, no conversion needed).
"""

import os
import asyncio
import time
from dataclasses import dataclass
from typing import Optional, Callable, Awaitable, List

from deepgram import AsyncDeepgramClient, DeepgramClientEnvironment

from .log import ServiceLogger

log = ServiceLogger("Speech")

_MAX_RECONNECTS      = int(os.getenv("FLUX_MAX_RECONNECTS",       "3"))
_RECONNECT_BASE_DELAY = float(os.getenv("FLUX_RECONNECT_BASE_DELAY", "1.0"))

_DEEPGRAM_MODEL    = os.getenv("DEEPGRAM_MODEL",    "flux-general-en")
_DEEPGRAM_LANGUAGE = os.getenv("DEEPGRAM_LANGUAGE", "").strip()
# flux-general-en → V2 client (no language param).
# nova-2 / nova-3  → V1 client (supports language param for multilingual STT).
_USE_V1 = _DEEPGRAM_MODEL != "flux-general-en"

_DEEPGRAM_REGION = os.getenv("DEEPGRAM_REGION", "us").lower()  # "us" or "eu"

_DEEPGRAM_EU = DeepgramClientEnvironment(
    base="wss://api.eu.deepgram.com",
    production="wss://api.eu.deepgram.com",
    agent="wss://agent.eu.deepgram.com",
)


def _deepgram_env() -> DeepgramClientEnvironment | None:
    """Return the DeepgramClientEnvironment for the configured region, or None for US default."""
    return _DEEPGRAM_EU if _DEEPGRAM_REGION == "eu" else None


# =============================================================================
# TRANSCRIBER
# =============================================================================

class Transcriber:
    """
    Deepgram Flux streaming transcriber.

    Reconnects automatically on unexpected disconnect (up to _MAX_RECONNECTS
    times with exponential backoff). Calls on_dead() if all attempts fail.
    """

    def __init__(
        self,
        on_end_of_turn:   Callable[[str], Awaitable[None]],
        on_start_of_turn: Callable[[], Awaitable[None]],
        on_interim:       Optional[Callable[[str], Awaitable[None]]] = None,
        on_dead:          Optional[Callable[[], Awaitable[None]]]    = None,
    ):
        self._on_end_of_turn   = on_end_of_turn
        self._on_start_of_turn = on_start_of_turn
        self._on_interim       = on_interim
        self._on_dead          = on_dead

        self._api_key:         str           = os.getenv("DEEPGRAM_API_KEY", "")
        self._client                         = None
        self._connection                     = None
        self._cm                             = None
        self._listener_task: Optional[asyncio.Task] = None
        self._running:       bool            = False
        self._reconnect_count: int           = 0
        self._reconnect_task: Optional[asyncio.Task] = None
        # V1 (nova-2) turn-detection state — accumulate is_final segments until speech_final
        self._v1_buf:          str           = ""
        self._v1_turn_started: bool          = False

    @property
    def is_active(self) -> bool:
        return self._running and self._connection is not None

    def bind(
        self,
        on_end_of_turn:   Callable[[str], Awaitable[None]],
        on_start_of_turn: Callable[[], Awaitable[None]],
        on_interim:       Optional[Callable[[str], Awaitable[None]]] = None,
        on_dead:          Optional[Callable[[], Awaitable[None]]]    = None,
    ) -> None:
        """Rebind callbacks — used by TranscriberPool when dispensing a warm connection."""
        self._on_end_of_turn   = on_end_of_turn
        self._on_start_of_turn = on_start_of_turn
        self._on_interim       = on_interim
        self._on_dead          = on_dead
        self._v1_buf           = ""
        self._v1_turn_started  = False

    async def start(self) -> None:
        if self._running:
            return
        try:
            env = _deepgram_env()
            self._client = AsyncDeepgramClient(
                api_key=self._api_key,
                **({'environment': env} if env else {}),
            )
            connect_kwargs = dict(model=_DEEPGRAM_MODEL, encoding="mulaw", sample_rate=8000)
            if _USE_V1 and _DEEPGRAM_LANGUAGE:
                connect_kwargs["language"] = _DEEPGRAM_LANGUAGE
            listener = self._client.listen.v1 if _USE_V1 else self._client.listen.v2
            self._cm = listener.connect(**connect_kwargs)
            self._connection = await self._cm.__aenter__()
            self._connection.on("message", self._on_message)
            self._connection.on("error",   self._on_error)
            self._connection.on("close",   self._on_close)
            self._listener_task = asyncio.create_task(self._connection.start_listening())
            self._running = True
            log.connected()
        except Exception as e:
            log.error("Connection failed", e)
            await self._cleanup()
            raise

    async def send(self, audio_bytes: bytes) -> None:
        if not self._connection or not self._running:
            return
        try:
            await asyncio.wait_for(self._connection.send_media(audio_bytes), timeout=0.1)
        except Exception as e:
            log.error("Send failed", e)
            self._running = False
            self._connection = None

    async def stop(self) -> None:
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
        self._client     = None

    async def _on_message(self, message, *args, **kwargs) -> None:
        try:
            msg_type = getattr(message, "type", None)

            if msg_type == "TurnInfo":
                # V2 (Flux) — turn boundaries are explicit
                event = getattr(message, "event", None)
                if event == "EndOfTurn":
                    transcript = (getattr(message, "transcript", "") or "").strip()
                    await self._on_end_of_turn(transcript)
                elif event == "StartOfTurn":
                    await self._on_start_of_turn()

            elif msg_type == "Results":
                # Extract transcript from channel alternatives
                ch = getattr(message, "channel", None)
                text = ""
                if ch:
                    alts = getattr(ch, "alternatives", None)
                    if alts:
                        alt = alts[0] if isinstance(alts, list) else alts
                        text = (getattr(alt, "transcript", "") or "").strip()

                if _USE_V1:
                    # V1 (nova-2): use is_final + speech_final for turn detection
                    is_final    = getattr(message, "is_final",    False)
                    speech_final = getattr(message, "speech_final", False)
                    if text and not is_final and not self._v1_turn_started:
                        # First interim result → start of turn
                        self._v1_turn_started = True
                        await self._on_start_of_turn()
                    if is_final and text:
                        self._v1_buf = (self._v1_buf + " " + text).strip()
                    if speech_final:
                        full = self._v1_buf.strip()
                        self._v1_buf = ""
                        self._v1_turn_started = False
                        if full:
                            await self._on_end_of_turn(full)
                    elif self._on_interim and text and not is_final:
                        await self._on_interim(text)
                else:
                    # V2: Results only used for interim display
                    if self._on_interim and text:
                        await self._on_interim(text)
        except Exception as e:
            log.error("Message handling failed", e)

    async def _on_close(self, *args, **kwargs) -> None:
        if self._running:
            log.warning("Deepgram connection closed unexpectedly")
            self._connection = None
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _on_error(self, error, *args, **kwargs) -> None:
        log.error("Deepgram error: " + str(error))
        self._connection = None
        if self._running:
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        while self._running and self._reconnect_count < _MAX_RECONNECTS:
            delay = _RECONNECT_BASE_DELAY * (2 ** self._reconnect_count)
            self._reconnect_count += 1
            log.warning(f"Reconnecting (attempt {self._reconnect_count}/{_MAX_RECONNECTS}) in {delay:.1f}s")
            await asyncio.sleep(delay)
            if not self._running:
                return
            try:
                await self._cleanup()
                env = _deepgram_env()
                self._client = AsyncDeepgramClient(
                    api_key=self._api_key,
                    **({'environment': env} if env else {}),
                )
                connect_kwargs = dict(model=_DEEPGRAM_MODEL, encoding="mulaw", sample_rate=8000)
                if _USE_V1 and _DEEPGRAM_LANGUAGE:
                    connect_kwargs["language"] = _DEEPGRAM_LANGUAGE
                listener = self._client.listen.v1 if _USE_V1 else self._client.listen.v2
                self._cm = listener.connect(**connect_kwargs)
                self._connection = await self._cm.__aenter__()
                self._connection.on("message", self._on_message)
                self._connection.on("error",   self._on_error)
                self._connection.on("close",   self._on_close)
                self._listener_task = asyncio.create_task(self._connection.start_listening())
                self._reconnect_count = 0
                log.connected()
                log.info("Reconnected successfully")
                return
            except Exception as e:
                log.error(f"Reconnect attempt {self._reconnect_count} failed", e)

        if self._running:
            log.error(f"Permanently unavailable after {_MAX_RECONNECTS} attempts — hanging up")
            self._running = False
            if self._on_dead:
                await self._on_dead()


# =============================================================================
# TRANSCRIBER POOL
# =============================================================================

async def _noop_transcript(_: str) -> None: pass
async def _noop_started()           -> None: pass


@dataclass
class _Entry:
    transcriber: Transcriber
    created_at:  float


class TranscriberPool:
    """
    Pre-warmed Transcriber connection pool.

    Deepgram connections take ~1.4s to establish — pre-warming eliminates
    that latency from every call. Auto-refills after each dispense or eviction.
    """

    def __init__(self, pool_size: int = 1, ttl: float = 120.0):
        self._pool_size = pool_size
        self._ttl       = ttl
        self._ready:     List[_Entry]            = []
        self._running:   bool                    = False
        self._fill_event = asyncio.Event()
        self._fill_task: Optional[asyncio.Task]  = None

    @property
    def available(self) -> int:
        return len(self._ready)

    async def start(self) -> None:
        if self._running:
            return
        self._running   = True
        self._fill_task = asyncio.create_task(self._fill_loop())

    async def get(
        self,
        on_end_of_turn:   Callable[[str], Awaitable[None]],
        on_start_of_turn: Callable[[], Awaitable[None]],
        on_dead:          Optional[Callable[[], Awaitable[None]]] = None,
    ) -> Transcriber:
        while self._ready:
            entry = self._ready.pop(0)
            age   = time.monotonic() - entry.created_at
            if age < self._ttl:
                entry.transcriber.bind(on_end_of_turn, on_start_of_turn, on_dead=on_dead)
                log.info(f"Dispensed warm connection (idle {int(age * 1000)}ms)")
                self._fill_event.set()
                return entry.transcriber
            else:
                log.info(f"Discarded stale connection (idle {int(age * 1000)}ms)")
                await entry.transcriber.stop()

        log.info("Pool empty — connecting fresh")
        t = Transcriber(on_end_of_turn=on_end_of_turn, on_start_of_turn=on_start_of_turn, on_dead=on_dead)
        await t.start()
        self._fill_event.set()
        return t

    async def stop(self) -> None:
        self._running = False
        self._fill_event.set()
        if self._fill_task:
            self._fill_task.cancel()
            try:
                await self._fill_task
            except asyncio.CancelledError:
                pass
            self._fill_task = None
        for entry in self._ready:
            await entry.transcriber.stop()
        self._ready.clear()

    async def _fill_loop(self) -> None:
        try:
            while self._running:
                await self._evict_stale()
                while self._running and len(self._ready) < self._pool_size:
                    t = Transcriber(on_end_of_turn=_noop_transcript, on_start_of_turn=_noop_started)
                    try:
                        await t.start()
                        self._ready.append(_Entry(transcriber=t, created_at=time.monotonic()))
                        log.info(f"Warm connection ready ({len(self._ready)}/{self._pool_size})")
                    except Exception as e:
                        log.error("Pre-connect failed", e)
                        await asyncio.sleep(2.0)
                self._fill_event.clear()
                try:
                    await asyncio.wait_for(self._fill_event.wait(), timeout=self._ttl / 2)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    async def _evict_stale(self) -> None:
        now   = time.monotonic()
        fresh = []
        for entry in self._ready:
            if time.monotonic() - entry.created_at < self._ttl:
                fresh.append(entry)
            else:
                log.info(f"Evicted stale connection (idle {int((now - entry.created_at) * 1000)}ms)")
                await entry.transcriber.stop()
        self._ready = fresh
