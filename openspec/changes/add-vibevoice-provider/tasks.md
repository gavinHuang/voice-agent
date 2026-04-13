## 1. Dependencies & Configuration

- [x] 1.1 Add `vibevoice[streamingtts]` as an optional dependency in `pyproject.toml` under `[project.optional-dependencies]` extras group `vibevoice`, pointing to the GitHub repo
- [x] 1.2 Add `VIBEVOICE_MODEL`, `VIBEVOICE_DEVICE` (and `TTS_PROVIDER=vibevoice` comment) to `.env.example`

## 2. Core Provider Implementation

- [x] 2.1 Create `shuo/voice_vibevoice.py` with a module-level `_MODEL` singleton and a `_load_model()` function that loads VibeVoice-Realtime-0.5B from `VIBEVOICE_MODEL` env var (default `"microsoft/VibeVoice-Realtime-0.5B"`) onto the device specified by `VIBEVOICE_DEVICE` (default `"cpu"`)
- [x] 2.2 Implement `VibeVoiceTTS.__init__` accepting `on_audio` and `on_done` callbacks, storing them and initializing internal state (`_running`, `_cancel_event`, `_generate_task`)
- [x] 2.3 Implement `bind(on_audio, on_done)` to rebind callbacks (required by `VoicePool`)
- [x] 2.4 Implement `start()` to trigger model load (via `_load_model()`) and set `_running = True`; model load runs in executor to avoid blocking the event loop
- [x] 2.5 Implement `send(text)` to buffer incoming text chunks into an internal queue for the streaming generator
- [x] 2.6 Implement `flush()` to signal end of text input to the generator, then await final audio completion and call `on_done`
- [x] 2.7 Implement `stop()` as `flush()` + cleanup (graceful shutdown)
- [x] 2.8 Implement `cancel()` to set `_cancel_event`, cancel any running `_generate_task`, and return immediately without calling `on_done`

## 3. Audio Conversion Pipeline

- [x] 3.1 Implement `_convert_chunk(pcm_float32: np.ndarray) -> str` that: (a) converts float32 → int16 via numpy, (b) downsamples 24kHz → 8kHz via `audioop.ratecv`, (c) encodes int16 → μ-law via `audioop.lin2ulaw`, (d) returns base64 string
- [x] 3.2 Implement `_generate_loop()` async method that runs model inference in `loop.run_in_executor`, iterates over yielded audio chunks, calls `_convert_chunk`, emits to `on_audio`, and respects `_cancel_event`
- [x] 3.3 Verify chunk size: buffer small chunks from VibeVoice to emit ≈160-sample (20ms at 8kHz) packets to `on_audio`

## 4. Factory & CLI Integration

- [x] 4.1 Add `elif provider == "vibevoice": from .voice_vibevoice import VibeVoiceTTS; return VibeVoiceTTS(on_audio, on_done)` branch to `_create_tts()` in `shuo/voice.py`
- [x] 4.2 Add `vibevoice` to the `TTS_PROVIDER` choices in `shuo/cli.py` (both `serve` and `call` commands if a `--tts-provider` flag exists, otherwise document the env var path)

## 5. Tests

- [x] 5.1 Add `tests/test_voice_vibevoice.py` with unit tests for `_convert_chunk`: verify output is valid base64 μ-law and correct length for a synthetic 24kHz sine input
- [x] 5.2 Add a mock-model test for `VibeVoiceTTS` verifying `bind()`, `start()`, and `cancel()` lifecycle without loading actual model weights (mock `_load_model`)
- [x] 5.3 Verify `_create_tts("vibevoice")` returns a `VibeVoiceTTS` instance (integration with factory, mock the model load)
- [x] 5.4 Run full test suite (`python -m pytest tests/ -v`) and confirm 133+ tests pass with no regressions

## 6. Documentation

- [x] 6.1 Update `CLAUDE.md` architecture table to include `voice_vibevoice.py` in the key modules list
- [x] 6.2 Add a `## VibeVoice Setup` section to `CLAUDE.md` (or a new `docs/vibevoice.md`) covering GPU requirements, install command, and env var configuration
