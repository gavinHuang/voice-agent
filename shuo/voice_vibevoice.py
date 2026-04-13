"""
VibeVoice TTS via local VibeVoice-Realtime-0.5B model.

Env vars:
    VIBEVOICE_MODEL      — HuggingFace Hub ID or local path
                           (default: microsoft/VibeVoice-Realtime-0.5B)
    VIBEVOICE_DEVICE     — compute device: cpu, cuda, mps (default: cpu)
    VIBEVOICE_VOICE      — path to a voice preset .pt file
                           (default: first English preset found in VIBEVOICE_VOICES_DIR)
    VIBEVOICE_VOICES_DIR — directory containing voice preset .pt files
    VIBEVOICE_CFG_SCALE  — classifier-free guidance scale (default: 1.5)
    VIBEVOICE_STEPS      — diffusion steps (default: 5)

Install:
    pip install 'vibevoice[streamingtts] @ git+https://github.com/microsoft/VibeVoice.git'
"""

import base64
import asyncio
import copy
import os
import threading
from typing import Optional, Callable, Awaitable

import numpy as np

try:
    import audioop
except ImportError:
    import audioop_lts as audioop  # type: ignore  # Python 3.13+

from .log import ServiceLogger

log = ServiceLogger("TTS")

# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_MODEL = None
_PROCESSOR = None
_ALL_PREFILLED = None   # loaded voice preset (KV-cache dict); deepcopy before each generate
_LOAD_LOCK = threading.Lock()

# 20 ms of audio at 24 kHz → 3:1 downsample → 160 samples @ 8 kHz per Twilio packet
_CHUNK_SAMPLES_24K = 480


def _find_voice_file() -> str:
    """Find a voice preset .pt file using env vars or package defaults."""
    explicit = os.getenv("VIBEVOICE_VOICE")
    if explicit:
        if not os.path.exists(explicit):
            raise FileNotFoundError(f"VIBEVOICE_VOICE file not found: {explicit!r}")
        return explicit

    voices_dir = os.getenv("VIBEVOICE_VOICES_DIR", "")
    if not voices_dir:
        # Try to locate voices shipped alongside the installed package source
        try:
            import vibevoice as _vv
            pkg_root = os.path.dirname(os.path.dirname(_vv.__file__))
            candidate = os.path.join(pkg_root, "demo", "voices", "streaming_model")
            if os.path.isdir(candidate):
                voices_dir = candidate
        except Exception:
            pass

    if not voices_dir or not os.path.isdir(voices_dir):
        raise RuntimeError(
            "No voice preset found. Set VIBEVOICE_VOICE=/path/to/preset.pt "
            "or VIBEVOICE_VOICES_DIR=/path/to/voices_dir, "
            "or run: bash demo/download_experimental_voices.sh"
        )

    # Prefer English voices, then any voice
    import glob
    presets = sorted(glob.glob(os.path.join(voices_dir, "*.pt")))
    en_presets = [p for p in presets if os.path.basename(p).startswith("en-")]
    chosen = en_presets[0] if en_presets else presets[0] if presets else None
    if not chosen:
        raise RuntimeError(f"No .pt voice files found in {voices_dir!r}")
    return chosen


def _load_model() -> None:
    """Load model, processor, and voice preset into module-level singletons (blocking)."""
    global _MODEL, _PROCESSOR, _ALL_PREFILLED
    if _MODEL is not None:
        return
    with _LOAD_LOCK:
        if _MODEL is not None:
            return

        import torch

        model_path = os.getenv("VIBEVOICE_MODEL", "microsoft/VibeVoice-Realtime-0.5B")
        device = os.getenv("VIBEVOICE_DEVICE", "cpu").lower()
        steps = int(os.getenv("VIBEVOICE_STEPS", "5"))

        try:
            from vibevoice import (  # type: ignore
                VibeVoiceStreamingForConditionalGenerationInference,
            )
            from vibevoice.processor.vibevoice_streaming_processor import (  # type: ignore
                VibeVoiceStreamingProcessor,
            )
        except ImportError as exc:
            raise ImportError(
                "VibeVoice is not installed. Run:\n"
                "  pip install 'vibevoice[streamingtts] @ "
                "git+https://github.com/microsoft/VibeVoice.git'"
            ) from exc

        log.info(f"Loading VibeVoice model {model_path!r} on {device!r} …")

        if device == "cuda":
            load_dtype = torch.bfloat16
            attn_impl = "flash_attention_2"
        else:
            load_dtype = torch.float32
            attn_impl = "sdpa"

        try:
            model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
                model_path,
                torch_dtype=load_dtype,
                device_map=device if device in ("cuda", "cpu") else None,
                attn_implementation=attn_impl,
            )
            if device == "mps":
                model.to("mps")
        except Exception as e:
            if attn_impl == "flash_attention_2":
                log.info(f"flash_attention_2 unavailable ({e}), retrying with sdpa")
                model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
                    model_path,
                    torch_dtype=load_dtype,
                    device_map=device if device in ("cuda", "cpu") else None,
                    attn_implementation="sdpa",
                )
            else:
                raise

        model.eval()
        model.set_ddpm_inference_steps(num_steps=steps)

        log.info("Loading VibeVoice processor …")
        processor = VibeVoiceStreamingProcessor.from_pretrained(model_path)

        voice_file = _find_voice_file()
        log.info(f"Loading voice preset {os.path.basename(voice_file)!r} …")
        all_prefilled = torch.load(voice_file, map_location=device, weights_only=False)

        _MODEL = model
        _PROCESSOR = processor
        _ALL_PREFILLED = all_prefilled
        log.info("VibeVoice ready")


def _get_singletons():
    if _MODEL is None:
        raise RuntimeError("VibeVoice model not loaded — call start() first")
    return _MODEL, _PROCESSOR, _ALL_PREFILLED


# ---------------------------------------------------------------------------
# Audio conversion
# ---------------------------------------------------------------------------

def _convert_chunk(pcm_float32: np.ndarray, ratecv_state=None) -> tuple[str, object]:
    """Convert a 24 kHz float32 numpy array to base64 μ-law 8 kHz.

    Returns (b64_string, new_ratecv_state).  Thread state through successive
    calls for proper streaming interpolation at chunk boundaries.
    """
    samples = np.clip(pcm_float32.flatten() * 32767.0, -32768, 32767).astype(np.int16)
    pcm_bytes = samples.tobytes()
    resampled, new_state = audioop.ratecv(pcm_bytes, 2, 1, 24000, 8000, ratecv_state)
    ulaw = audioop.lin2ulaw(resampled, 2)
    return base64.b64encode(ulaw).decode(), new_state


# ---------------------------------------------------------------------------
# TTS provider class
# ---------------------------------------------------------------------------

class VibeVoiceTTS:
    """VibeVoice-Realtime-0.5B local TTS.

    Implements the same start/send/flush/stop/cancel/bind interface as
    ElevenLabsTTS, KokoroTTS, and FishAudioTTS — drops directly into VoicePool.

    Text is accumulated via send() and synthesis starts on flush().
    Audio streams back incrementally via AsyncAudioStreamer while generation runs.
    """

    def __init__(
        self,
        on_audio: Callable[[str], Awaitable[None]],
        on_done: Callable[[], Awaitable[None]],
    ):
        self._on_audio = on_audio
        self._on_done = on_done
        self._running = False
        self._text_parts: list[str] = []
        self._generate_task: Optional[asyncio.Task] = None
        self._cancel_event = asyncio.Event()
        self._ratecv_state = None

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
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _load_model)
        self._running = True
        self._text_parts = []
        self._cancel_event = asyncio.Event()
        self._ratecv_state = None
        log.connected()

    async def send(self, text: str) -> None:
        if not self._running:
            return
        self._text_parts.append(text)

    async def flush(self) -> None:
        if not self._running:
            return
        full_text = "".join(self._text_parts)
        self._text_parts = []
        if full_text:
            self._generate_task = asyncio.create_task(self._generate_loop(full_text))
            try:
                await self._generate_task
            except asyncio.CancelledError:
                return
        if self._running:
            self._running = False
            await self._on_done()

    async def stop(self) -> None:
        if not self._running:
            return
        await self.flush()
        log.disconnected()

    async def cancel(self) -> None:
        self._running = False
        self._cancel_event.set()
        if self._generate_task and not self._generate_task.done():
            self._generate_task.cancel()
            try:
                await self._generate_task
            except asyncio.CancelledError:
                pass
        self._generate_task = None
        log.cancelled()

    # ------------------------------------------------------------------
    # Generation loop
    # ------------------------------------------------------------------

    async def _generate_loop(self, text: str) -> None:
        import torch
        from vibevoice.modular.streamer import AsyncAudioStreamer  # type: ignore

        model, processor, all_prefilled = _get_singletons()
        cfg_scale = float(os.getenv("VIBEVOICE_CFG_SCALE", "1.5"))
        device = os.getenv("VIBEVOICE_DEVICE", "cpu").lower()

        # Prepare inputs (fast, runs on event loop thread)
        inputs = processor.process_input_with_cached_prompt(
            text=text,
            cached_prompt=all_prefilled,
            padding=True,
            return_tensors="pt",
            return_attention_mask=True,
        )
        for k, v in inputs.items():
            if torch.is_tensor(v):
                inputs[k] = v.to(device)

        # AsyncAudioStreamer uses asyncio.Queue + call_soon_threadsafe to bridge
        # the blocking generate thread → async event loop
        streamer = AsyncAudioStreamer(batch_size=1, stop_signal=None, timeout=60.0)

        def _run_generate():
            model.generate(
                **inputs,
                max_new_tokens=None,
                cfg_scale=cfg_scale,
                tokenizer=processor.tokenizer,
                generation_config={"do_sample": False},
                audio_streamer=streamer,
                return_speech=False,
                all_prefilled_outputs=copy.deepcopy(all_prefilled),
            )

        loop = asyncio.get_event_loop()
        gen_future = loop.run_in_executor(None, _run_generate)

        buffer = np.array([], dtype=np.float32)

        try:
            async for chunk in streamer.get_stream(0):
                if self._cancel_event.is_set():
                    break

                if isinstance(chunk, np.ndarray):
                    audio = chunk.flatten()
                else:
                    # torch.Tensor
                    audio = chunk.numpy().flatten()

                buffer = np.concatenate([buffer, audio])

                # Emit in ~20 ms packets (480 samples @ 24 kHz → 160 @ 8 kHz)
                while len(buffer) >= _CHUNK_SAMPLES_24K and not self._cancel_event.is_set():
                    emit = buffer[:_CHUNK_SAMPLES_24K]
                    buffer = buffer[_CHUNK_SAMPLES_24K:]
                    b64, self._ratecv_state = _convert_chunk(emit, self._ratecv_state)
                    await self._on_audio(b64)

            # Drain remaining samples after stream ends
            if len(buffer) > 0 and not self._cancel_event.is_set():
                b64, self._ratecv_state = _convert_chunk(buffer, self._ratecv_state)
                await self._on_audio(b64)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("Generation failed", e)
        finally:
            # Always wait for the generate thread to finish
            try:
                await gen_future
            except Exception:
                pass
