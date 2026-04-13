## Why

The platform currently depends on third-party TTS providers (ElevenLabs, Kokoro, Fish) which introduce per-request costs and external API latency. VibeVoice-Realtime-0.5B is a self-hostable, open-source TTS model from Microsoft with ~200ms first-audio latency, streaming text input, and MIT license — a strong fit for cost-sensitive or latency-sensitive deployments.

## What Changes

- Add `shuo/voice_vibevoice.py` implementing a `VoiceVibeVoice` provider that streams text into VibeVoice-Realtime-0.5B and returns 24kHz PCM audio chunks
- Integrate with the existing `VoicePool` / `AudioPlayer` pipeline in `voice.py` so it is interchangeable with existing TTS providers
- Add `vibevoice` as a valid value for the `TTS_PROVIDER` env var / `--tts-provider` CLI flag
- Wire model loading at server startup (pre-warm, similar to existing pool pattern)
- Document installation requirements (GPU recommended; PyTorch + VibeVoice package)

Note: VibeVoice also contains an ASR component, but the linked Realtime-0.5B model is TTS-only. STT integration (replacing Deepgram) is out of scope for this change.

## Capabilities

### New Capabilities
- `vibevoice-tts`: Local, self-hosted TTS via VibeVoice-Realtime-0.5B — streaming text input, ~200ms TTFA, 24kHz PCM output, no per-request API cost

### Modified Capabilities
- `cli-call`: New `--tts-provider vibevoice` option propagated through CLI and config

## Impact

- **New file**: `shuo/voice_vibevoice.py`
- **Modified**: `shuo/voice.py` (register new provider), `shuo/cli.py` (add `vibevoice` to provider choices), `shuo/.env.example`
- **New dependency**: `vibevoice[streamingtts]` (PyTorch-based; GPU recommended, CPU feasible for dev)
- **No breaking changes** — existing providers unchanged; vibevoice is opt-in via env/CLI
