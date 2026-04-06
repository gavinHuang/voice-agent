# Codebase Concerns

**Analysis Date:** 2026-04-06 (updated after greenfield module refactor + bug fixes)

> **Note:** Several concerns from the original 2026-03-18 audit were resolved during the refactor phases:
> - BUG-01 (`_dtmf_pending` race): fixed — `_dtmf_lock: asyncio.Lock` in `web.py`
> - BUG-02 (TTS pool TOCTOU): fixed — `_lock: asyncio.Lock` in `VoicePool`
> - BUG-03 (token observer blocking): fixed — `asyncio.call_soon()` in `agent.py`
> - BUG-04 (inactivity watchdog): fixed — `_inactivity_watchdog()` in `call.py`
> - Dashboard auth: fixed — `verify_api_key` dependency in `monitor/server.py`
> - Twilio signature validation: fixed — `verify_twilio_signature` dependency in `web.py`
> - Trace file rotation: fixed — `cleanup_traces()` in `tracer.py`
> - pydantic-ai migration: complete — `LanguageModel` uses typed tool calls; no MarkerScanner

## Tech Debt

**Flux (Deepgram) Connection Not Pooled:**
- Issue: Deepgram connections cannot be reused because reusing an idle connection causes the turn detector to fire prematurely. Each call creates a fresh `Transcriber`.
- Files: `shuo/speech.py` (TranscriberPool is present but pool size stays at 1 per call)
- Impact: First turn latency is degraded vs. TTS; ~1.4s connection setup per call
- Fix approach: Contact Deepgram to clarify turn detector calibration; implement re-calibration workaround

**Missing Error Recovery in LanguageModel:**
- Issue: `LanguageModel._generate()` catches generic `Exception` but doesn't distinguish retry-able from non-retry-able failures. All errors call `on_done()`.
- Files: `shuo/language.py`
- Impact: Transient Groq timeouts cause silent failure; user may hear silence
- Fix approach: Exponential backoff retry for transient errors; log error details

**No Fallback LLM Provider:**
- Issue: Groq is the sole LLM provider. No fallback to OpenAI or Anthropic if Groq is unavailable.
- Files: `shuo/language.py`
- Impact: Groq API outage = complete system outage
- Fix approach: Support multiple providers via `LLM_MODEL` prefix (e.g., `openai:gpt-4o`); implement failover

**FastAPI `on_event` Deprecation:**
- Issue: `shuo/web.py` uses `@app.on_event("startup")` and `@app.on_event("shutdown")` which are deprecated in favor of `lifespan=` context manager.
- Files: `shuo/web.py`
- Impact: 2 deprecation warnings in test output (benign for now; will break in future FastAPI)
- Fix approach: Migrate to `@asynccontextmanager` lifespan pattern

**Agent History Grows Unbounded:**
- Problem: Conversation history accumulates in `Agent._llm.history` without truncation. Long calls increase LLM input token usage and latency.
- Files: `shuo/agent.py`, `shuo/language.py`
- Impact: Turn latency increases as history grows; LLM billing scales with history size
- Improvement path: History truncation (keep last N turns); optional summarization

## Known Bugs

**DTMF Redirect May Lose Context on Reconnect:**
- Symptoms: Agent presses DTMF key, call is redirected. If reconnection is delayed or fails, the saved DTMF state (`_dtmf_pending[call_sid]`) is orphaned.
- Files: `shuo/web.py` (DTMF pending logic and reconnect handler)
- Trigger: Slow DTMF reconnect, server restart during redirect, multiple rapid DTMF sequences
- Workaround: Add explicit timeout/expiration to `_dtmf_pending` entries

**WebSocket Error Messages Not Propagated Clearly:**
- Symptoms: Errors during Twilio WebSocket reads are caught and logged but not surfaced to the caller; call silently disconnects.
- Files: `shuo/call.py` (`run_call()` reader task)
- Trigger: Twilio network hiccup, client disconnect, WebSocket protocol error

## Security Considerations

**Environment Variable Exposure in Error Messages:**
- Risk: Exception messages in LLM or TTS services might include API keys if not redacted
- Files: `shuo/language.py`, `shuo/speech.py`
- Mitigation: `log.py` redacts common secrets, but not all error paths are covered

**DTMF Injection (Controlled Risk):**
- Risk: LanguageModel can call `press_dtmf(digit)` to send phone tones. Digits are constrained by pydantic-ai tool schema (0-9, *, #) but no rate limiting beyond tool call frequency.
- Files: `shuo/language.py` (tool definition), `shuo/web.py` (DTMF endpoint)
- Mitigation: pydantic-ai schema constrains digit format; system prompt constrains behavior

**Takeover Mode Authentication:**
- Risk: Any client with a valid dashboard API key can trigger takeover
- Files: `monitor/server.py` (takeover endpoint)
- Mitigation: `DASHBOARD_API_KEY` env var protects the endpoint; designed for single-operator use

## Performance Bottlenecks

**Deepgram Flux Connection Startup Latency:**
- Problem: ~1.4s latency per call because Flux connections cannot be pre-warmed
- Files: `shuo/speech.py` (Transcriber startup)
- Cause: Turn detector calibration issue with idle connections
- Improvement path: Contact Deepgram; implement silent audio re-calibration on warmup

**TTS Connection Pool Sizing:**
- Problem: Pool size hardcoded at 2 for the global pool in `web.py`. Under high concurrency (10+ calls), pool is exhausted and new calls cold-start.
- Files: `shuo/web.py` (warmup), `shuo/voice.py` (VoicePool)
- Improvement path: Configurable pool size via env var

## Fragile Areas

**State Reconnection Logic After Takeover:**
- Files: `shuo/web.py` (`get_saved_state` callback, DTMF reconnect handler)
- Why fragile: Complex conditional logic checking multiple sources (DTMF pending, existing call by call_sid, history). Race conditions possible if two streams reconnect simultaneously for the same call_sid.
- Safe modification: Add comprehensive tests for concurrent reconnection scenarios

**Monitor Event Bus and Registry Synchronization:**
- Files: `monitor/bus.py`, `monitor/registry.py`
- Why fragile: In-memory dictionaries are mutated directly without locks. WebSocket broadcasts are async but mutations are sync.
- Safe modification: Protect registry mutations with asyncio.Lock; add soft-delete tombstone pattern

## Scaling Limits

**Memory Usage in Long Conversations:**
- Current capacity: ~100 turns before noticeable latency degradation
- Limit: Agent history stored in-process as pydantic-ai ModelMessage list
- Scaling path: Server-side conversation history DB (PostgreSQL); sliding window (last 50 turns)

**Concurrent Call Capacity:**
- Current capacity: ~10-20 concurrent calls (depends on VoicePool size and Deepgram connections)
- Limit: Single process
- Scaling path: Kubernetes + multiple replicas; shared pools via Redis

## Dependencies at Risk

**Groq API:**
- Risk: Sole LLM provider; no fallback
- Impact: Groq outage = complete system outage

**Deepgram Flux API:**
- Risk: Continuous connection required; no batching or caching
- Impact: Network interruption causes reconnect delay and potential dropped turns

**ElevenLabs TTS (Default):**
- Risk: Free tier rate-limited; high cost at scale
- Impact: Pool exhaustion, calls fail with timeout
- Mitigation: `TTS_PROVIDER=kokoro` for local zero-cost fallback

## Test Coverage Gaps

**Concurrent Takeover:**
- What's not tested: Two supervisors taking over the same call simultaneously
- Files: `monitor/server.py`, `shuo/web.py` (takeover logic)
- Priority: High

**WebSocket Reconnection After Server Restart:**
- What's not tested: Graceful handling when server restarts during active call
- Priority: High

**LLM Service Cancellation Edge Cases:**
- What's not tested: pydantic-ai streaming cancellation; partial history append when cancelled
- Files: `shuo/language.py`
- Priority: Medium

---

*Concerns audit updated: 2026-04-06*
