## Context

The platform has three TTS providers (ElevenLabs, Kokoro, Fish). All share a common async interface: `start / send / flush / stop / cancel / bind`. The `VoicePool` in `voice.py` pre-warms instances of whatever provider is selected via `TTS_PROVIDER` and `_create_tts()`.

Twilio consumes audio as base64-encoded **μ-law 8 kHz** mono PCM. ElevenLabs delivers this natively; Kokoro/Fish also produce it. VibeVoice outputs **24 kHz float32 PCM**, so conversion is required.

VibeVoice-Realtime-0.5B is a local PyTorch model (~1 GB, BF16). Unlike cloud WebSocket providers, it runs in-process. It supports streaming text input and yields audio chunks incrementally.

## Goals / Non-Goals

**Goals:**
- Implement `VibeVoiceTTS` satisfying the existing TTS provider interface
- Convert VibeVoice 24 kHz float32 output → μ-law 8 kHz for Twilio via `audioop`
- Load model once at startup; reuse across calls (no per-call model init)
- Support streaming text input to minimize TTFA
- Integrate with `_create_tts()` and `VoicePool` unchanged
- Add `vibevoice` to `TTS_PROVIDER` env var and CLI choices

**Non-Goals:**
- VibeVoice ASR / STT integration (out of scope)
- Multi-speaker support (model is single-speaker)
- GPU auto-selection beyond `VIBEVOICE_DEVICE` env var

## Decisions

### 1. Singleton model, not per-instance

**Decision:** Load the VibeVoice model once into a module-level singleton (`_MODEL`), shared across all `VibeVoiceTTS` instances.

**Rationale:** The model is ~1 GB. Creating a new instance per TTS session (as ElevenLabs does with WebSocket connections) would re-load it every call. A singleton loaded at `start()` of the first instance (or at server startup via `VoicePool._fill_loop`) amortizes load time across all calls.

**Alternative considered:** Load per-instance (like ElevenLabs) — rejected because re-loading a 1 GB model every ~30 seconds is unacceptable.

### 2. Run inference in `asyncio.get_event_loop().run_in_executor()`

**Decision:** PyTorch/VibeVoice inference is synchronous and CPU/GPU-bound. Wrap the generate call in `loop.run_in_executor(None, ...)` to avoid blocking the asyncio event loop during synthesis.

**Rationale:** All other I/O (WebSocket, phone audio) is async. Blocking the event loop during synthesis would stall barge-in detection and audio playback of other calls.

**Alternative considered:** `asyncio.to_thread` — equivalent on Python 3.9+; either works. Using `run_in_executor` for compatibility.

### 3. Audio conversion: resample 24kHz → 8kHz, float32 → int16 → μ-law

**Decision:** Convert VibeVoice output with `numpy` (float32 → int16) + `audioop.ratecv` (24kHz → 8kHz) + `audioop.lin2ulaw` (int16 → μ-law). Emit chunks of ~20ms (160 samples at 8kHz) matching Twilio's expected packet cadence.

**Rationale:** `audioop` is already used in `voice.py` for DTMF. Resampling ratio is 3:1 (24000/8000), exact integer ratio — no quality loss from `ratecv`.

**Alternative considered:** `torchaudio.functional.resample` — adds a torchaudio dependency call on the hot path; `audioop.ratecv` is sufficient and avoids GPU round-trips for a simple 3:1 downsample.

### 4. VoicePool compatibility via `bind()`

**Decision:** `VibeVoiceTTS` implements `bind(on_audio, on_done)` to rebind callbacks when dispensed from the pool, identical to existing providers.

**Rationale:** `VoicePool.get()` calls `bind()` on warm instances. The model singleton means warm instances are cheap to hold — just a Python object with references to the shared model.

### 5. `VIBEVOICE_MODEL` env var for model path

**Decision:** Read model ID/path from `VIBEVOICE_MODEL` env var, defaulting to `"microsoft/VibeVoice-Realtime-0.5B"`. Accept both HuggingFace Hub IDs and local paths.

**Rationale:** Production deployments will want a local model path to avoid HuggingFace downloads on startup. Dev defaults to the Hub ID for zero-config setup.

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| First-call cold start if model not pre-loaded | `VoicePool.start()` at server startup triggers `_fill_loop` → `_create_tts` → `VibeVoiceTTS.start()` → model load before first call |
| CPU inference latency exceeds target ~400ms e2e | Recommend GPU via `VIBEVOICE_DEVICE=cuda`; document requirement; CPU feasible for dev/testing |
| Single-speaker English-only model | Documented limitation; non-English callers should use other providers with `CALLER_LANG` translation |
| VibeVoice package not in PyPI (install from GitHub) | Document `pip install git+https://github.com/microsoft/VibeVoice.git#egg=vibevoice[streamingtts]`; add to `pyproject.toml` as optional dep |
| PyTorch version conflicts | Pin VibeVoice as an optional extras group (`[vibevoice]`) in `pyproject.toml` to avoid polluting base install |

## Migration Plan

1. Install: `uv add --optional vibevoice "vibevoice[streamingtts] @ git+https://github.com/microsoft/VibeVoice.git"` (or update `pyproject.toml` manually)
2. Set `TTS_PROVIDER=vibevoice` (and optionally `VIBEVOICE_MODEL`, `VIBEVOICE_DEVICE`)
3. No schema/data migration needed — purely additive
4. Rollback: revert `TTS_PROVIDER` to previous value

## Open Questions

- Does VibeVoice's streaming Python API yield numpy arrays or tensors? Need to confirm chunk shape from the GitHub demo code to finalize the conversion pipeline.
- What is the minimum chunk size VibeVoice emits? This determines whether we need to buffer before emitting to Twilio (Twilio expects ~20ms / 160-sample chunks).
- GPU memory footprint under concurrent calls — model is shared singleton but inference may not be parallelizable on a single GPU. May need a semaphore for concurrent synthesis.
