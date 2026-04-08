## Context

The platform already has a `Tracer` class (`shuo/tracer.py`) that records turn-level spans and markers (LLM latency, TTS latency, first-token time). However, it only tracks per-turn timing within agent turns — it does not capture call-level lifecycle events (dial, phone connection, STT ready, first STT result, TTS first chunk, hangup, etc.).

The `/trace/latest` endpoint in `web.py` returns the raw per-turn trace JSON. There is no structured call-level summary: no aggregate counts (turns completed, STT events, TTS segments), no wall-clock durations between lifecycle milestones, and no single summary object emitted at call end.

The new telemetry layer must sit alongside — not replace — the existing `Tracer`. It records call-scoped milestones as named checkpoints with wall-clock timestamps and computes a summary at call end.

## Goals / Non-Goals

**Goals:**
- Record named checkpoints throughout a call's lifecycle (script/prompt gen start, phone connect, STT ready, first STT result, TTS first chunk, turn boundaries, hangup, etc.)
- Compute durations between key checkpoint pairs (e.g. dial→connect, connect→first-word, user-spoke→first-audio)
- Emit a structured `CallSummary` at call end via logger and the existing `/trace/latest` endpoint
- Keep instrumentation non-intrusive: modules call `telemetry.checkpoint(name)` without needing to coordinate

**Non-Goals:**
- Replace the existing `Tracer` per-turn span system — both coexist
- External metrics backends (Prometheus, Datadog) — out of scope for this change
- Persistent storage beyond the existing `/tmp/shuo/` trace files

## Decisions

### Decision 1: Extend `Tracer` vs. new `CallTelemetry` class

**Choice**: New `CallTelemetry` class in `shuo/telemetry.py`.

**Rationale**: `Tracer` is turn-scoped (keyed by turn number) with a span/marker model. Call-level telemetry is lifecycle-scoped with a flat checkpoint list. Mixing them would complicate both APIs. A separate class with a simple `checkpoint(name)` interface is easier to instrument across modules and keeps `Tracer` unchanged.

**Alternative**: Extend `Tracer` with call-level markers — rejected because `Tracer` already has a well-tested API consumed by tests and `web.py`; changing its shape risks breaking existing consumers.

### Decision 2: Attachment to call context

**Choice**: `CallTelemetry` is created in `run_call()` alongside `Tracer` and passed to `Agent` (and via `Agent` to `LanguageModel` and TTS). `speech.py`'s `Transcriber` receives it via constructor or a setter at startup.

**Rationale**: `run_call()` is already the wiring point for `Tracer`, `VoicePool`, and `TranscriberPool`. Adding `telemetry` here follows established patterns. No global state is needed.

**Alternative**: Thread-local / context-var based singleton — rejected to avoid hidden state and test complexity.

### Decision 3: Summary format and delivery

**Choice**: At call end, `CallTelemetry.summary()` returns a structured dict that is (a) logged at INFO level and (b) merged into the existing trace JSON written by `Tracer.save()`.

**Rationale**: Reuses the existing trace file infrastructure and `/trace/latest` endpoint without new endpoints. The summary is a flat dict of checkpoint timestamps and computed duration pairs — easy to parse in scripts or dashboards.

### Decision 4: Checkpoint naming convention

**Choice**: `snake_case` string literals, e.g. `"call_connected"`, `"stt_ready"`, `"llm_start"`, `"tts_first_chunk"`, `"hangup"`. A small constants module (or Enum) in `telemetry.py` defines the canonical names.

**Rationale**: Avoids typo-driven silent gaps. Modules import constants; free-form strings are also allowed for ad-hoc points.

## Risks / Trade-offs

- [Thread-safety] `CallTelemetry` is accessed from async coroutines in the same event loop — no locking needed. If off-thread TTS providers ever call back from a thread pool, `time.monotonic()` and `list.append()` are GIL-safe in CPython.
  → Mitigation: document the single-loop assumption; add a note if fish/kokoro TTS callbacks become threaded.

- [Overhead] Each checkpoint is a `time.monotonic()` call + list append — negligible (~1 µs).
  → No mitigation needed.

- [Missed checkpoints] If a module doesn't call `checkpoint()`, the summary silently omits that stage.
  → Mitigation: define a required-checkpoint list; `summary()` logs a warning for any missing required checkpoint.

## Migration Plan

1. Add `shuo/telemetry.py` with `CallTelemetry` class and checkpoint constants.
2. Instrument `run_call()` for call-lifecycle checkpoints (connect, hangup, STT ready).
3. Instrument `Agent` / `LanguageModel` for LLM turn checkpoints (prompt start/end, first token).
4. Instrument TTS providers for TTS checkpoints (synthesis start, first chunk, last chunk).
5. Instrument `Transcriber` for STT checkpoints (ready, first result per turn).
6. At call end in `run_call()`, call `telemetry.summary()`, log it, and merge into `Tracer.save()` output.
7. Update `/trace/latest` in `web.py` to include `call_summary` key from merged trace file.

No rollback needed — the feature is additive. Disabling is as simple as not constructing `CallTelemetry`.

## Open Questions

- Should DTMF send events be checkpointed? (Likely yes for IVR flows — add as optional checkpoint.)
- Should per-turn telemetry (LLM tokens received count, TTS segments count) be aggregated in the call summary? (Proposed: yes, as simple counters.)
