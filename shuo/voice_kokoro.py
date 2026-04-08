"""
Kokoro-82M TTS via the kokoro Python package (direct inference).

Requires kokoro to be installed: uv add kokoro (Python <3.13 only).

Env vars:
    KOKORO_VOICE     — voice name (default af_heart)
    KOKORO_REPO_ID   — HuggingFace repo or local path (default hexgrad/Kokoro-82M)
"""

import os
import base64
import asyncio
import audioop
from typing import Optional, Callable, Awaitable

from .log import ServiceLogger

log = ServiceLogger("TTS")

TARGET_RATE = 8000
KOKORO_SAMPLE_RATE = 24000

# Module-level pipeline — loaded once, shared across all KokoroTTS instances.
_pipeline = None


def _load_pipeline() -> object:
    from kokoro import KPipeline
    repo_id = os.getenv("KOKORO_REPO_ID", "hexgrad/Kokoro-82M")
    return KPipeline(lang_code="a", repo_id=repo_id)


async def _get_pipeline() -> object:
    global _pipeline
    if _pipeline is None:
        log.info("Loading Kokoro model (first use)...")
        _pipeline = await asyncio.to_thread(_load_pipeline)
        log.info("Kokoro model ready.")
    return _pipeline


class KokoroTTS:
    """
    Kokoro TTS via direct KPipeline inference.

    Buffers text from send(), runs the pipeline on flush(),
    converts 24 kHz float32 PCM → μ-law 8 kHz for Twilio.
    """

    def __init__(
        self,
        on_audio: Callable[[str], Awaitable[None]],
        on_done: Callable[[], Awaitable[None]],
    ):
        self._on_audio = on_audio
        self._on_done = on_done

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._text_buffer = ""
        self._ratecv_state: object = None

        self._voice = os.getenv("KOKORO_VOICE", "af_heart")

    @property
    def is_active(self) -> bool:
        return self._running

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
        # Pre-warm the model so it's ready when flush() is called.
        await _get_pipeline()
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

    # ── Core generation ─────────────────────────────────────────────

    async def _generate(self, text: str) -> None:
        pipeline = await _get_pipeline()
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        voice = self._voice

        def run_sync() -> None:
            try:
                for _gs, _ps, audio in pipeline(text, voice=voice):
                    if not self._running:
                        break
                    # audio: torch.Tensor or numpy float32 at 24000 Hz
                    import torch
                    if isinstance(audio, torch.Tensor):
                        audio = audio.cpu().numpy()
                    import numpy as np
                    pcm = (audio * 32767).clip(-32768, 32767).astype("int16").tobytes()
                    loop.call_soon_threadsafe(queue.put_nowait, ("audio", pcm))
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, ("error", e))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

        asyncio.get_event_loop().run_in_executor(None, run_sync)

        try:
            while True:
                kind, data = await queue.get()
                if kind == "audio":
                    if self._running:
                        await self._process_pcm(data)
                elif kind == "error":
                    log.error("Generation failed", data)
                    break
                elif kind == "done":
                    break
        except asyncio.CancelledError:
            raise
        finally:
            if self._running:
                await self._on_done()

    async def _process_pcm(self, pcm_data: bytes) -> None:
        resampled, self._ratecv_state = audioop.ratecv(
            pcm_data, 2, 1, KOKORO_SAMPLE_RATE, TARGET_RATE, self._ratecv_state,
        )
        ulaw_data = audioop.lin2ulaw(resampled, 2)
        await self._on_audio(base64.b64encode(ulaw_data).decode())
