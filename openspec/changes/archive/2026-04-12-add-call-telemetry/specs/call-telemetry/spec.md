## ADDED Requirements

### Requirement: Checkpoint recording
The system SHALL provide a `CallTelemetry` class that records named checkpoints with monotonic timestamps throughout a call's lifecycle. Each checkpoint SHALL have a unique string name and be recorded at most once (duplicate names are silently ignored or overwritten with a warning).

#### Scenario: Record a checkpoint
- **WHEN** any module calls `telemetry.checkpoint("call_connected")`
- **THEN** the checkpoint is stored with the current `time.monotonic()` timestamp

#### Scenario: Duplicate checkpoint
- **WHEN** `telemetry.checkpoint(name)` is called with a name already recorded
- **THEN** the first timestamp is kept and a warning is logged

### Requirement: Canonical checkpoint names
The system SHALL define canonical checkpoint name constants covering at minimum: `script_generation_start`, `script_generation_end`, `call_dial`, `call_connected`, `stt_ready`, `stt_first_result`, `llm_start`, `llm_first_token`, `llm_end`, `tts_synthesis_start`, `tts_first_chunk`, `tts_playback_done`, `hangup`.

#### Scenario: Canonical names available
- **WHEN** a module imports `from shuo.telemetry import CP`
- **THEN** `CP.CALL_CONNECTED` and all other canonical names are accessible as string constants

### Requirement: Counter recording
The system SHALL allow modules to increment named integer counters via `telemetry.increment(name, amount=1)` to track aggregate event counts (e.g. total STT results received, total TTS segments played, total LLM turns completed).

#### Scenario: Increment a counter
- **WHEN** `telemetry.increment("stt_results")` is called three times
- **THEN** `telemetry.summary()["counters"]["stt_results"]` equals 3

### Requirement: Call summary generation
The system SHALL produce a structured call summary dict via `telemetry.summary()` that includes: all recorded checkpoints with their timestamps relative to `call_connected` (in milliseconds), all counter values, and computed durations for key checkpoint pairs.

#### Scenario: Summary includes relative timestamps
- **WHEN** `call_connected` is recorded and later `stt_first_result` is recorded
- **THEN** `summary()["checkpoints"]["stt_first_result_ms"]` is the elapsed ms since `call_connected`

#### Scenario: Summary includes computed durations
- **WHEN** both `llm_start` and `llm_first_token` checkpoints are recorded
- **THEN** `summary()["durations"]["llm_ttft_ms"]` equals the ms between those two checkpoints

#### Scenario: Missing required checkpoint warning
- **WHEN** `summary()` is called and one or more required checkpoints were never recorded
- **THEN** a warning is logged listing the missing checkpoint names; those keys are absent from the summary

### Requirement: Telemetry attached to call context
The system SHALL create one `CallTelemetry` instance per call in `run_call()` and pass it to `Agent`, `Transcriber`, and `LanguageModel` so all modules share the same instance without global state.

#### Scenario: Single telemetry instance per call
- **WHEN** a call starts and `run_call()` initializes the call
- **THEN** exactly one `CallTelemetry` is created and passed down to all instrumented subsystems

### Requirement: Call-lifecycle checkpoints instrumented
The system SHALL record the following checkpoints automatically during every call without requiring caller code changes: `call_connected` (on `CallStartedEvent`), `stt_ready` (after transcriber starts), `hangup` (on `HangupEvent` or `CallEndedEvent`).

#### Scenario: call_connected recorded on stream start
- **WHEN** `CallStartedEvent` is processed in the call loop
- **THEN** `telemetry.checkpoint(CP.CALL_CONNECTED)` is called before any agent turn starts

#### Scenario: hangup recorded at call end
- **WHEN** the call loop exits (either via `HangupEvent` or `CallEndedEvent`)
- **THEN** `telemetry.checkpoint(CP.HANGUP)` is recorded in the `finally` block

### Requirement: LLM turn checkpoints instrumented
The system SHALL record `llm_start` at the beginning of each LLM streaming call and `llm_first_token` when the first text token is received.

#### Scenario: LLM TTFT captured per turn
- **WHEN** an agent turn starts and the LLM begins streaming
- **THEN** `llm_start` and `llm_first_token` checkpoints are recorded for that turn (first turn only if multiple turns occur)

### Requirement: TTS checkpoints instrumented
The system SHALL record `tts_synthesis_start` when a TTS synthesis request begins and `tts_first_chunk` when the first audio chunk is received from the TTS provider.

#### Scenario: TTS first-chunk latency captured
- **WHEN** TTS synthesis begins for an agent turn
- **THEN** `tts_synthesis_start` is recorded before the request and `tts_first_chunk` is recorded when audio data first arrives

### Requirement: STT checkpoints instrumented
The system SHALL record `stt_first_result` the first time the transcriber fires `on_end_of_turn` with a non-empty transcript during the call.

#### Scenario: STT first result captured
- **WHEN** the transcriber delivers the first complete user utterance
- **THEN** `telemetry.checkpoint(CP.STT_FIRST_RESULT)` is recorded exactly once

### Requirement: Summary emitted at call end
The system SHALL log the call summary at INFO level and merge it into the trace JSON file (as a `"call_summary"` key) written by `Tracer.save()` at the end of every call.

#### Scenario: Summary logged at call end
- **WHEN** the call loop's `finally` block executes
- **THEN** `telemetry.summary()` is called, the result is logged at INFO level, and the trace JSON file includes a `"call_summary"` key with the summary dict

#### Scenario: Summary visible via trace endpoint
- **WHEN** `/trace/latest` is requested after a call ends
- **THEN** the response JSON includes a `"call_summary"` object with checkpoint timestamps, durations, and counters
