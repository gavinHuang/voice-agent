"""
voice.py — Text-to-speech, audio playback, and DTMF tone generation.

VoicePool: pre-warmed TTS connections (eliminates cold-start latency).
AudioPlayer: streams audio chunks to the phone at real-time rate.
dtmf_tone(): generates a DTMF digit as μ-law audio for IVR navigation.

TTS provider is selected via TTS_PROVIDER env var:
    kokoro      — Kokoro-82M via Python package (local, default)
    fish        — Fish Audio self-hosted
    elevenlabs  — ElevenLabs cloud API

All providers share the same interface: start/send/flush/cancel/stop/bind.
"""

import base64
import asyncio
import os
import time
from dataclasses import dataclass
from typing import Optional, Callable, Awaitable, List

import numpy as np

try:
    import audioop
except ImportError:
    import audioop_lts as audioop  # type: ignore  # Python 3.13+

from .log import ServiceLogger

log = ServiceLogger("Voice")


# =============================================================================
# DTMF TONE GENERATOR
# =============================================================================

_DTMF_FREQS: dict[str, tuple[int, int]] = {
    '1': (697, 1209), '2': (697, 1336), '3': (697, 1477),
    '4': (770, 1209), '5': (770, 1336), '6': (770, 1477),
    '7': (852, 1209), '8': (852, 1336), '9': (852, 1477),
    '*': (941, 1209), '0': (941, 1336), '#': (941, 1477),
}


def dtmf_tone(digit: str, duration_ms: int = 200) -> str:
    """Return a DTMF tone for `digit` as base64-encoded μ-law 8kHz audio."""
    freqs = _DTMF_FREQS.get(digit)
    if freqs is None:
        raise ValueError(f"Unknown DTMF digit: {digit!r}")
    f1, f2 = freqs
    n  = int(8000 * duration_ms / 1000)
    t  = np.arange(n) / 8000.0
    sig = np.sin(2 * np.pi * f1 * t) + np.sin(2 * np.pi * f2 * t)
    pcm = (sig / 2.0 * 32767).astype(np.int16).tobytes()
    return base64.b64encode(audioop.lin2ulaw(pcm, 2)).decode()


# =============================================================================
# TTS FACTORY
# =============================================================================

def _create_tts(
    on_audio: Callable[[str], Awaitable[None]],
    on_done:  Callable[[], Awaitable[None]],
):
    """Create a TTS service instance for the configured provider."""
    provider = os.getenv("TTS_PROVIDER", "kokoro").lower()
    if provider == "kokoro":
        from .voice_kokoro import KokoroTTS
        return KokoroTTS(on_audio, on_done)
    elif provider == "fish":
        from .voice_fish import FishAudioTTS
        return FishAudioTTS(on_audio, on_done)
    elif provider == "elevenlabs":
        from .voice_elevenlabs import ElevenLabsTTS
        return ElevenLabsTTS(on_audio, on_done)
    else:
        raise ValueError(f"Unknown TTS_PROVIDER: {provider!r}")


# =============================================================================
# AUDIO PLAYER
# =============================================================================

class AudioPlayer:
    """
    Drips audio chunks to the phone at real-time playback rate.

    Fires on_done() when the last chunk finishes playing — this is when
    the phone call actually hears the end of the agent's turn, not when
    the last byte was buffered.
    """

    def __init__(
        self,
        phone,
        on_done: Optional[Callable[[], None]] = None,
        stream_sid: str = "",  # kept for API compatibility
    ):
        self._phone   = phone
        self._on_done = on_done

        self._chunks:   List[str]                = []
        self._task:     Optional[asyncio.Task]   = None
        self._running:  bool                     = False
        self._index:    int                      = 0
        self._tts_done: bool                     = False

    @property
    def is_playing(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    async def send_chunk(self, chunk: str) -> None:
        """Append an audio chunk; starts playback on first chunk."""
        if not self._running:
            self._chunks   = []
            self._index    = 0
            self._running  = True
            self._tts_done = False
            self._task     = asyncio.create_task(self._playback_loop())
        self._chunks.append(chunk)

    def mark_tts_done(self) -> None:
        """Signal that TTS has flushed — no more chunks coming."""
        self._tts_done = True
        # If playback never started (no audio produced), fire completion now.
        if self._task is None and self._on_done:
            self._on_done()

    async def stop_and_clear(self) -> None:
        """Stop immediately and flush the phone's audio buffer."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task     = None
        self._chunks   = []
        self._index    = 0
        self._tts_done = False
        await self._phone.send_clear()

    async def play(self, chunks: List[str]) -> None:
        """Start playing a fixed list of chunks (legacy / testing path)."""
        if self.is_playing:
            await self.stop_and_clear()
        self._chunks   = list(chunks)
        self._index    = 0
        self._running  = True
        self._tts_done = True
        self._task     = asyncio.create_task(self._playback_loop())

    async def _playback_loop(self) -> None:
        try:
            while self._running:
                if self._index < len(self._chunks):
                    chunk = self._chunks[self._index]
                    await self._phone.send_audio(chunk)
                    self._index += 1
                    duration_s = len(base64.b64decode(chunk)) / 8000.0
                    await asyncio.sleep(max(duration_s, 0.010))
                elif self._tts_done:
                    break
                else:
                    await asyncio.sleep(0.010)
            if self._running:
                self._running = False
                if self._on_done:
                    self._on_done()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("Playback failed", e)
            self._running = False


# =============================================================================
# VOICE POOL
# =============================================================================

async def _noop_audio(_: str) -> None: pass
async def _noop_done()          -> None: pass


@dataclass
class _Entry:
    tts:        object
    created_at: float


class VoicePool:
    """
    Pre-warmed TTS connection pool.

    Eliminates cold-start latency by keeping a warm connection ready.
    Auto-refills after each dispense. Evicts stale connections past TTL.
    """

    def __init__(self, pool_size: int = 1, ttl: float = 8.0):
        self._pool_size  = pool_size
        self._ttl        = ttl
        self._ready:     List[_Entry]            = []
        self._running:   bool                    = False
        self._fill_event = asyncio.Event()
        self._fill_task: Optional[asyncio.Task]  = None
        self._lock       = asyncio.Lock()

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
        on_audio: Callable[[str], Awaitable[None]],
        on_done:  Callable[[], Awaitable[None]],
    ):
        while True:
            async with self._lock:
                entry = self._ready.pop(0) if self._ready else None
            if entry is None:
                break
            age = time.monotonic() - entry.created_at
            if age < self._ttl:
                entry.tts.bind(on_audio, on_done)
                log.info(f"Dispensed warm TTS (idle {int(age * 1000)}ms)")
                self._fill_event.set()
                return entry.tts
            else:
                log.info(f"Discarded stale TTS (idle {int(age * 1000)}ms)")
                await entry.tts.cancel()

        log.info("Pool empty — connecting fresh TTS")
        tts = _create_tts(on_audio=on_audio, on_done=on_done)
        await tts.start()
        self._fill_event.set()
        return tts

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
        async with self._lock:
            entries = list(self._ready)
            self._ready.clear()
        for entry in entries:
            await entry.tts.cancel()

    async def _fill_loop(self) -> None:
        try:
            while self._running:
                await self._evict_stale()
                while self._running and len(self._ready) < self._pool_size:
                    tts = _create_tts(on_audio=_noop_audio, on_done=_noop_done)
                    try:
                        await tts.start()
                        self._ready.append(_Entry(tts=tts, created_at=time.monotonic()))
                        log.info(f"Warm TTS ready ({len(self._ready)}/{self._pool_size})")
                    except Exception as e:
                        log.error("Pre-connect failed", e)
                        await asyncio.sleep(1.0)
                self._fill_event.clear()
                try:
                    await asyncio.wait_for(self._fill_event.wait(), timeout=self._ttl / 2)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    async def _evict_stale(self) -> None:
        now   = time.monotonic()
        stale = []
        async with self._lock:
            fresh = []
            for entry in self._ready:
                if time.monotonic() - entry.created_at < self._ttl:
                    fresh.append(entry)
                else:
                    stale.append(entry)
            self._ready = fresh
        for entry in stale:
            log.info(f"Evicted stale TTS (idle {int((now - entry.created_at) * 1000)}ms)")
            await entry.tts.cancel()
