"""
Kokoro-82M TTS via the kokoro Python package (direct inference).

Requires kokoro to be installed: uv add kokoro (Python <3.13 only).

Env vars:
    KOKORO_VOICE     — voice name (default af_heart)
    KOKORO_REPO_ID   — HuggingFace repo or local path (default hexgrad/Kokoro-82M)

Streaming behaviour
-------------------
Tokens arrive one at a time via send(). When a sentence boundary is detected
(. ! ?) the accumulated text is queued for immediate synthesis so Kokoro can
generate audio while the LLM is still producing tokens for the next sentence.
flush() drains any remaining text and signals end-of-turn.

A single _drain_task processes segments in order, maintaining the audioop
ratecv state across segments so resampling is seamless.
"""

import os
import re
import base64
import asyncio
from typing import Optional, Callable, Awaitable

try:
    import audioop
except ImportError:
    import audioop_lts as audioop  # type: ignore  # Python 3.13+

from .log import ServiceLogger

log = ServiceLogger("TTS")

TARGET_RATE = 8000
KOKORO_SAMPLE_RATE = 24000

# Flush accumulated text to Kokoro when a sentence ends.
# Require at least 10 chars so we don't synthesize single punctuation marks.
_SENTENCE_END_RE = re.compile(r'[.!?]+\s*$')
_MIN_SEGMENT_LEN = 10

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
    Kokoro TTS with sentence-boundary streaming.

    Text tokens are buffered in send(). When a sentence ends (. ! ?) the
    segment is enqueued immediately so synthesis overlaps with LLM generation.
    flush() queues any remaining text and sends a sentinel to finish the turn.

    A _drain_task processes the queue in order, streaming audio chunks as
    Kokoro yields them. Calling cancel() stops synthesis mid-flight.
    """

    def __init__(
        self,
        on_audio: Callable[[str], Awaitable[None]],
        on_done: Callable[[], Awaitable[None]],
    ):
        self._on_audio = on_audio
        self._on_done = on_done

        self._running = False
        self._voice = os.getenv("KOKORO_VOICE", "af_heart")

        self._text_buffer: str = ""
        self._ratecv_state: object = None
        self._segment_queue: asyncio.Queue = asyncio.Queue()
        self._drain_task: Optional[asyncio.Task] = None

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
        await _get_pipeline()
        self._running = True
        self._text_buffer = ""
        self._ratecv_state = None
        self._segment_queue = asyncio.Queue()
        self._drain_task = asyncio.create_task(self._drain_loop())
        log.connected()

    async def send(self, text: str) -> None:
        if not self._running:
            return
        self._text_buffer += text
        # Flush on sentence boundary so synthesis starts before LLM finishes
        if _SENTENCE_END_RE.search(text) and len(self._text_buffer.strip()) >= _MIN_SEGMENT_LEN:
            await self._enqueue_buffer()

    async def flush(self) -> None:
        if not self._running:
            return
        if self._text_buffer.strip():
            await self._enqueue_buffer()
        await self._segment_queue.put(None)  # EOT sentinel

    async def stop(self) -> None:
        if not self._running:
            return
        try:
            await self.flush()
            if self._drain_task and not self._drain_task.done():
                await self._drain_task
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

    # ── Internal ────────────────────────────────────────────────────

    async def _enqueue_buffer(self) -> None:
        text = self._text_buffer
        self._text_buffer = ""
        await self._segment_queue.put(text)

    async def _drain_loop(self) -> None:
        """Drain segment queue in order; None sentinel = end of turn."""
        try:
            while True:
                segment = await self._segment_queue.get()
                if segment is None:
                    break
                if self._running:
                    await self._synthesize(segment)
        except asyncio.CancelledError:
            raise
        finally:
            if self._running:
                self._running = False
                await self._on_done()

    async def _synthesize(self, text: str) -> None:
        """Run Kokoro on text in a thread, stream audio chunks as they arrive."""
        pipeline = await _get_pipeline()
        loop = asyncio.get_event_loop()
        chunk_q: asyncio.Queue = asyncio.Queue()
        voice = self._voice
        running_ref = [self._running]  # mutable ref for thread

        def run_sync() -> None:
            try:
                for _gs, _ps, audio in pipeline(text, voice=voice):
                    if not running_ref[0] or not self._running:
                        break
                    import torch
                    if isinstance(audio, torch.Tensor):
                        audio = audio.cpu().numpy()
                    import numpy as np
                    pcm = (audio * 32767).clip(-32768, 32767).astype("int16").tobytes()
                    loop.call_soon_threadsafe(chunk_q.put_nowait, ("chunk", pcm))
            except Exception as e:
                loop.call_soon_threadsafe(chunk_q.put_nowait, ("error", e))
            finally:
                loop.call_soon_threadsafe(chunk_q.put_nowait, ("done", None))

        loop.run_in_executor(None, run_sync)

        while True:
            kind, data = await chunk_q.get()
            if kind == "chunk":
                if self._running:
                    await self._process_pcm(data)
            elif kind == "error":
                log.error("Synthesis failed", data)
                break
            elif kind == "done":
                break

    async def _process_pcm(self, pcm_data: bytes) -> None:
        resampled, self._ratecv_state = audioop.ratecv(
            pcm_data, 2, 1, KOKORO_SAMPLE_RATE, TARGET_RATE, self._ratecv_state,
        )
        ulaw_data = audioop.lin2ulaw(resampled, 2)
        await self._on_audio(base64.b64encode(ulaw_data).decode())

    async def _cleanup(self) -> None:
        self._running = False
        if self._drain_task and not self._drain_task.done():
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
            self._drain_task = None
