"""
Fish Audio S2 TTS via self-hosted HTTP server.

Env vars:
    FISH_AUDIO_URL          — base URL (default http://localhost:8080)
    FISH_AUDIO_REFERENCE_ID — voice reference ID (optional)
"""

import os
import base64
import asyncio
from typing import Optional, Callable, Awaitable

import audioop
import httpx

from .log import ServiceLogger

log = ServiceLogger("TTS")

TARGET_RATE = 8000
PCM_CHUNK = 4000  # bytes (~45 ms at 44.1 kHz)


class FishAudioTTS:
    """Fish Audio S2 streaming TTS — WAV stream → μ-law 8 kHz for Twilio."""

    def __init__(
        self,
        on_audio: Callable[[str], Awaitable[None]],
        on_done: Callable[[], Awaitable[None]],
    ):
        self._on_audio = on_audio
        self._on_done = on_done

        self._client: Optional[httpx.AsyncClient] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

        self._text_buffer = ""
        self._ratecv_state: object = None

        self._base_url = os.getenv("FISH_AUDIO_URL", "http://localhost:8080")
        self._reference_id = os.getenv("FISH_AUDIO_REFERENCE_ID", "")

    @property
    def is_active(self) -> bool:
        return self._running and self._client is not None

    def bind(
        self,
        on_audio: Callable[[str], Awaitable[None]],
        on_done: Callable[[], Awaitable[None]],
    ) -> None:
        self._on_audio = on_audio
        self._on_done = on_done

    async def start(self) -> None:
        if self._running:
            return
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
        self._running = True
        self._text_buffer = ""
        self._ratecv_state = None
        log.connected()

    async def send(self, text: str) -> None:
        if not self._running:
            return
        self._text_buffer += text

    async def flush(self) -> None:
        if not self._running or not self._text_buffer.strip():
            if self._running:
                await self._on_done()
            return
        text = self._text_buffer
        self._text_buffer = ""
        self._ratecv_state = None
        self._task = asyncio.create_task(self._generate(text))

    async def stop(self) -> None:
        if not self._running:
            return
        try:
            await self.flush()
            if self._task and not self._task.done():
                await self._task
        except Exception as e:
            log.error("Stop failed", e)
        finally:
            await self._cleanup()
        log.disconnected()

    async def cancel(self) -> None:
        self._running = False
        self._text_buffer = ""
        await self._cleanup()
        log.cancelled()

    async def _cleanup(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _generate(self, text: str) -> None:
        if not self._client:
            return

        payload: dict = {"text": text, "format": "wav", "streaming": True}
        if self._reference_id:
            payload["reference_id"] = self._reference_id

        try:
            async with self._client.stream(
                "POST", f"{self._base_url}/v1/tts", json=payload
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    log.error(f"Fish Audio error {resp.status_code}: {body[:200]}")
                    await self._on_done()
                    return

                header_buf = b""
                header_parsed = False
                source_rate = 44100
                pcm_buf = b""

                async for chunk in resp.aiter_bytes(4096):
                    if not self._running:
                        break
                    if not header_parsed:
                        header_buf += chunk
                        if len(header_buf) >= 44:
                            source_rate = int.from_bytes(header_buf[24:28], "little")
                            idx = header_buf.find(b"data")
                            offset = (idx + 8) if idx >= 0 and idx + 8 <= len(header_buf) else 44
                            pcm_buf = header_buf[offset:]
                            header_parsed = True
                            log.info(f"Streaming at {source_rate} Hz")
                        continue

                    pcm_buf += chunk
                    while len(pcm_buf) >= PCM_CHUNK:
                        await self._process_pcm(pcm_buf[:PCM_CHUNK], source_rate)
                        pcm_buf = pcm_buf[PCM_CHUNK:]

                if pcm_buf and self._running:
                    if len(pcm_buf) % 2:
                        pcm_buf = pcm_buf[:-1]
                    if pcm_buf:
                        await self._process_pcm(pcm_buf, source_rate)

            if self._running:
                await self._on_done()

        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("Generation failed", e)
            if self._running:
                await self._on_done()

    async def _process_pcm(self, pcm_data: bytes, source_rate: int) -> None:
        resampled, self._ratecv_state = audioop.ratecv(
            pcm_data, 2, 1, source_rate, TARGET_RATE, self._ratecv_state,
        )
        ulaw_data = audioop.lin2ulaw(resampled, 2)
        await self._on_audio(base64.b64encode(ulaw_data).decode())
