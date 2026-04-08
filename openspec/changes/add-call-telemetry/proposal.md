## Why

The voice agent platform currently lacks structured telemetry for individual call tasks — there is no way to understand how long each stage (script generation, phone connection, STT, TTS, hangup, etc.) takes within a single call, making it difficult to diagnose latency bottlenecks or track call health. Adding per-call telemetry meters provides observability data that surfaces automatically at call end.

## What Changes

- Introduce a `CallTelemetry` class that records timestamped checkpoints (meters) throughout a call's lifecycle
- Instrument key call stages: script/prompt generation, outbound dial, phone connection, STT recognition events, TTS synthesis, audio playback, turn boundaries, and call hangup
- Attach telemetry to each call's context so all modules can record meters without passing extra arguments
- Emit a structured telemetry summary (durations, counts, timing breakdowns) at call end via logging and the `/trace/latest` endpoint

## Capabilities

### New Capabilities

- `call-telemetry`: Per-call meter collection and reporting — tracks timestamped checkpoints across all key call stages, computes durations between stages, and emits a summary report at call end

### Modified Capabilities

<!-- No existing spec-level requirement changes -->

## Impact

- `shuo/call.py` — add telemetry context to `CallState`; record meters in `run_call()`
- `shuo/agent.py` — record TTS and LLM turn meters
- `shuo/speech.py` — record STT recognition meters
- `shuo/phone.py` — record dial and connection meters
- `shuo/language.py` — record script/prompt generation meters
- `shuo/web.py` — expose telemetry in `/trace/latest` response
- `shuo/tracer.py` — integrate or replace with new telemetry system (no breaking API change)
- New file: `shuo/telemetry.py`
