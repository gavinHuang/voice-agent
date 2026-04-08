## 1. Core Telemetry Module

- [x] 1.1 Create `shuo/telemetry.py` with `CallTelemetry` class: `checkpoint(name)`, `increment(name, amount)`, `summary()` methods
- [x] 1.2 Define `CP` constants class in `shuo/telemetry.py` with all canonical checkpoint names (`SCRIPT_GENERATION_START`, `SCRIPT_GENERATION_END`, `CALL_DIAL`, `CALL_CONNECTED`, `STT_READY`, `STT_FIRST_RESULT`, `LLM_START`, `LLM_FIRST_TOKEN`, `LLM_END`, `TTS_SYNTHESIS_START`, `TTS_FIRST_CHUNK`, `TTS_PLAYBACK_DONE`, `HANGUP`)
- [x] 1.3 Implement `summary()` to compute relative-ms timestamps (relative to `call_connected`), key duration pairs (`llm_ttft_ms`, `dial_to_connect_ms`, `connect_to_first_word_ms`, `user_spoke_to_first_audio_ms`), and counters
- [x] 1.4 Add missing-required-checkpoint warning in `summary()`

## 2. Call Loop Instrumentation

- [x] 2.1 Instantiate `CallTelemetry` in `run_call()` alongside `Tracer`
- [x] 2.2 Record `CP.CALL_CONNECTED` on `CallStartedEvent` in `run_call()`
- [x] 2.3 Record `CP.STT_READY` after transcriber `start()` completes in `run_call()`
- [x] 2.4 Record `CP.HANGUP` in the `finally` block of `run_call()` before `tracer.save()`
- [x] 2.5 Record `CP.CALL_DIAL` in `phone.py` / `dial_out()` when the outbound dial is initiated

## 3. Agent and LLM Instrumentation

- [x] 3.1 Pass `telemetry` to `Agent.__init__()` and store as `self._telemetry`
- [x] 3.2 Record `CP.LLM_START` in `language.py` / `LanguageModel` before the LLM streaming call begins (first turn only; increment counter every turn)
- [x] 3.3 Record `CP.LLM_FIRST_TOKEN` in `language.py` when the first streamed text token arrives (first turn only)
- [x] 3.4 Record `CP.LLM_END` after LLM stream completes; increment `"llm_turns"` counter each turn
- [x] 3.5 Record `CP.SCRIPT_GENERATION_START` / `CP.SCRIPT_GENERATION_END` around system-prompt / goal construction in `Agent` or `LanguageModel`

## 4. TTS Instrumentation

- [x] 4.1 Pass `telemetry` to TTS provider(s) (`voice_elevenlabs.py`, `voice_kokoro.py`, `voice_fish.py`) via `VoicePool` or per-synthesis call
- [x] 4.2 Record `CP.TTS_SYNTHESIS_START` before each TTS synthesis request; increment `"tts_segments"` counter
- [x] 4.3 Record `CP.TTS_FIRST_CHUNK` when the first audio bytes arrive from TTS (first synthesis only)
- [x] 4.4 Record `CP.TTS_PLAYBACK_DONE` when `AudioPlayer` signals playback complete (first turn only)

## 5. STT Instrumentation

- [x] 5.1 Pass `telemetry` to `Transcriber.__init__()` (add optional parameter, default `None`)
- [x] 5.2 Record `CP.STT_FIRST_RESULT` in `Transcriber.on_end_of_turn` callback on the first non-empty transcript received

## 6. Summary Emission and Trace Integration

- [x] 6.1 In `run_call()` `finally` block, call `telemetry.summary()`, log it at INFO level
- [x] 6.2 Modify `Tracer.save()` to accept an optional `call_summary` dict and merge it as `"call_summary"` key in the trace JSON output
- [x] 6.3 Update `run_call()` to pass `telemetry.summary()` into `tracer.save()`
- [x] 6.4 Verify `/trace/latest` in `web.py` returns the `"call_summary"` key without additional endpoint changes

## 7. Tests

- [x] 7.1 Unit test `CallTelemetry`: checkpoint recording, duplicate handling, counter increment, `summary()` output shape
- [x] 7.2 Unit test `summary()` duration calculations with synthetic timestamps
- [x] 7.3 Unit test missing-checkpoint warning path
- [x] 7.4 Verify existing 133 tests still pass after instrumentation changes
