"""
Audio player for streaming audio to Twilio.

Manages its own independent playback loop that drips audio
chunks at the correct rate, regardless of other activity.
"""

import base64
import asyncio
from typing import List, Optional, Callable

from ..log import ServiceLogger

log = ServiceLogger("Player")


class AudioPlayer:
    """
    Streams audio to Twilio at the correct rate.
    
    Features:
    - Independent playback loop (not affected by incoming messages)
    - Can be topped up with audio chunks dynamically (for streaming TTS)
    - Instant stop and clear on interrupt
    - Callback when playback completes
    """
    
    def __init__(
        self,
        isp,
        stream_sid: str = "",
        on_done: Optional[Callable[[], None]] = None,
    ):
        self._isp = isp
        self._on_done = on_done
        
        self._chunks: List[str] = []
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._index = 0
        self._tts_done = False
    
    @property
    def is_playing(self) -> bool:
        return self._running and self._task is not None and not self._task.done()
    
    async def start(self) -> None:
        """Start the playback loop."""
        if self.is_playing:
            await self.stop_and_clear()
        
        self._chunks = []
        self._index = 0
        self._running = True
        self._tts_done = False
        
        self._task = asyncio.create_task(self._playback_loop())
    
    async def send_chunk(self, chunk: str) -> None:
        """Add an audio chunk to the playback queue."""
        if not self._running:
            await self.start()
        
        self._chunks.append(chunk)
    
    def mark_tts_done(self) -> None:
        """Signal that TTS is complete - no more chunks coming."""
        self._tts_done = True
    
    async def play(self, chunks: List[str]) -> None:
        """Start playing a fixed list of audio chunks (legacy mode)."""
        if self.is_playing:
            await self.stop_and_clear()
        
        self._chunks = list(chunks)
        self._index = 0
        self._running = True
        self._tts_done = True
        
        self._task = asyncio.create_task(self._playback_loop())
    
    async def stop_and_clear(self) -> None:
        """Stop playback immediately and clear Twilio's buffer."""
        self._running = False
        
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        self._task = None
        self._chunks = []
        self._index = 0
        self._tts_done = False
        
        await self._send_clear()
    
    async def wait_until_done(self) -> None:
        """Wait for playback to complete (or be interrupted)."""
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
    
    async def _playback_loop(self) -> None:
        """
        Drips audio to Twilio at real-time playback rate.

        Each chunk is base64-encoded μ-law 8kHz audio (1 byte = 1 sample =
        0.125 ms). We sleep for the actual audio duration of each chunk so
        _on_done fires when the callee finishes hearing the last word, not
        when the last byte has been buffered in Twilio.
        """
        try:
            while self._running:
                if self._index < len(self._chunks):
                    chunk = self._chunks[self._index]
                    await self._send_audio(chunk)
                    self._index += 1
                    # Sleep for the actual audio duration of this chunk
                    audio_bytes = base64.b64decode(chunk)
                    duration_s = len(audio_bytes) / 8000.0
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
    
    async def _send_audio(self, payload: str) -> None:
        """Send a single audio chunk via ISP."""
        await self._isp.send_audio(payload)

    async def _send_clear(self) -> None:
        """Send clear message via ISP to flush audio buffer."""
        await self._isp.send_clear()
