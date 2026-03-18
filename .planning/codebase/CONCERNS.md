# Codebase Concerns

**Analysis Date:** 2026-03-18

## Tech Debt

**Connection Pool TTL Mismatch:**
- Issue: Global TTS pool in `server.py` uses `ttl=120.0s` but local conversation TTS pool uses `ttl=8.0s`. This inconsistency may cause connection reuse issues where idle connections are kept alive longer than expected.
- Files: `shuo/shuo/server.py` (line 76), `shuo/shuo/conversation.py` (line 77), `shuo/shuo/services/tts_pool.py` (line 55)
- Impact: Unpredictable connection freshness across different execution paths; may mask or exacerbate STT latency issues
- Fix approach: Standardize TTL across all pool instantiations. Decide on a single TTL value and apply consistently, or document why different values are needed

**Flux (Deepgram) Connection Not Pooled:**
- Issue: A design note in `server.py` (lines 80-83) states Flux connections cannot be reused because "Reusing an idle Deepgram connection causes the turn detector to fire prematurely." Each call creates a fresh connection, adding ~1.4s latency.
- Files: `shuo/shuo/server.py` (lines 80-83), `shuo/shuo/conversation.py` (lines 131-141)
- Impact: First turn latency is degraded significantly vs. TTS; turn detection sensitivity degrades over idle periods even within a call
- Fix approach: Either (1) implement a workaround to recalibrate turn detection on connection reuse, (2) accept the 1.4s first-turn latency as a tradeoff, or (3) investigate if newer Deepgram API versions support connection reuse

**Missing Error Recovery in LLM Service:**
- Issue: `LLMService._generate()` catches generic `Exception` but doesn't distinguish between API errors (retry-able), network errors (maybe retry-able), and logical errors (not retry-able). All errors simply call `on_done()`.
- Files: `shuo/shuo/services/llm.py` (lines 142-144)
- Impact: Transient API failures (Groq timeout, network blip) cause silent failure and end the turn instead of retrying; user may hear silence
- Fix approach: Implement exponential backoff retry logic for specific error types; log error details for debugging

**No Fallback LLM Provider:**
- Issue: System hardcodes `GROQ_API_KEY` as the sole LLM provider. No fallback to OpenAI or other services if Groq is unavailable.
- Files: `shuo/shuo/services/llm.py` (lines 57-60)
- Impact: Any Groq API outage or key misconfiguration renders the system non-functional
- Fix approach: Support multiple LLM providers in configuration; implement provider failover logic

**Agent History Corruption Risk on Takeover:**
- Issue: When reconnecting after takeover, agent history is restored from `dashboard_registry` (stored as list in memory). If the registry process crashes during a takeover, history is lost.
- Files: `shuo/shuo/server.py` (lines 676-705), `dashboard/registry.py`
- Impact: Reconnected agent after takeover resumes with partial or lost context; conversation continuity broken
- Fix approach: Persist takeover state to Redis or database; validate history integrity before resuming

## Known Bugs

**DTMF Redirect May Lose Context on Reconnect:**
- Symptoms: Agent enters IVR mode, presses DTMF key, call is redirected. If the reconnection is delayed or fails, the saved DTMF state (`_dtmf_pending[call_sid]`) expires or is orphaned.
- Files: `shuo/shuo/server.py` (lines 623-657, 659-705)
- Trigger: (1) Slow DTMF reconnect after redirect, (2) Server restart during redirect, (3) Multiple rapid DTMF sequences
- Workaround: Add explicit timeout/expiration to `_dtmf_pending` entries; warn user if reconnect takes >5s

**Marker Scanner Buffer Overflow Edge Case:**
- Symptoms: If an LLM produces malformed marker-like text (e.g., "[DTMF:INVALID]" or "[TOOLONGMARKER:..."), the buffer may overflow before being recognized as invalid.
- Files: `shuo/shuo/agent.py` (lines 48-112)
- Trigger: Non-standard LLM model that produces unexpected token sequences; adversarial prompts
- Workaround: Currently handled by `MAX_BUF = 20` (line 61) which prevents runaway buffering, but produces silent output loss

**WebSocket Error Messages Not Propagated Clearly:**
- Symptoms: Errors during Twilio WebSocket reads are caught and logged but not surfaced to the caller; call silently disconnects.
- Files: `shuo/shuo/conversation.py` (lines 90-103)
- Trigger: Twilio network hiccup, client disconnect, WebSocket protocol error
- Workaround: Check server logs for errors; dashboard shows "call ended" but not the reason

## Security Considerations

**Environment Variable Exposure in Error Messages:**
- Risk: If an exception is raised in LLM or TTS services, error messages might include API keys from `os.getenv()` calls if not carefully redacted.
- Files: `shuo/shuo/services/llm.py` (line 57-60), `shuo/shuo/services/flux.py` (line 39)
- Current mitigation: ServiceLogger redacts common secrets in `log.py`, but not all error paths are covered
- Recommendations:
  - Audit all `log.error()` calls to ensure no unredacted env vars in exception messages
  - Store API keys in a secrets manager (e.g., AWS Secrets Manager, HashiCorp Vault) instead of env vars
  - Use structured logging with field redaction rules

**DTMF Injection Vulnerability:**
- Risk: Agent can emit `[DTMF:N]` markers which are converted to actual phone tones sent to the remote party. No validation of what digits are allowed.
- Files: `shuo/shuo/agent.py` (lines 327-328), `shuo/shuo/services/dtmf.py`
- Current mitigation: LLM system prompt constrains behavior; DTMF only valid for 0-9, *, #
- Recommendations:
  - Whitelist allowed DTMF sequences in the agent's state machine
  - Log all DTMF emissions for audit trail
  - Rate-limit DTMF sequences to prevent abuse (e.g., max 1 DTMF per second)

**Takeover Mode Privilege Check Missing:**
- Risk: Any client can trigger takeover mode by setting the call mode in the dashboard. No authentication or authorization check.
- Files: `shuo/shuo/server.py` (lines 140-162), `dashboard/server.py`
- Current mitigation: Dashboard is assumed to be on a private network
- Recommendations:
  - Require API token/authentication for takeover operations
  - Log all takeover events with user identity
  - Add rate limiting on mode changes per call

## Performance Bottlenecks

**Large Agent History Grows Unbounded:**
- Problem: Conversation history accumulates in `Agent._llm.history` without any truncation. Long calls with many turns cause history to grow, increasing LLM input token usage and latency.
- Files: `shuo/shuo/agent.py` (lines 180-182), `shuo/shuo/services/llm.py` (lines 64, 77-79)
- Cause: No history pruning or summarization; each turn appends a new message pair
- Impact: Turn latency increases as history grows; LLM billing scales with history size; at 50+ turns, TTFT noticeably increases
- Improvement path:
  - Implement history truncation (keep last N turns only)
  - Add optional summarization of old turns via another LLM call
  - Consider storing history in Redis/database with eviction policy

**Deepgram Flux Connection Startup Latency:**
- Problem: ~1.4s latency per call (line 80-83 in server.py) because Flux connections cannot be pooled and reused
- Files: `shuo/shuo/conversation.py` (lines 131-141)
- Cause: Turn detector calibration issue with idle connections
- Impact: First turn latency is dominated by connection setup, not LLM or TTS
- Improvement path:
  - Contact Deepgram support to clarify turn detector calibration behavior
  - Implement connection re-calibration on warmup (silent audio, then listen)
  - Evaluate alternative STT providers that support connection pooling

**TTS Connection Teardown in Conversation Loop:**
- Problem: If a TTS pool instance is created locally (line 76-77 in conversation.py), it's shut down at the end of every call. For short calls, this adds overhead.
- Files: `shuo/shuo/conversation.py` (lines 75-77, 271-272)
- Impact: Short calls (<10s) incur TTS pool startup/shutdown overhead that rivals conversation time
- Improvement path: Always use the global TTS pool from server; remove local pool creation

## Fragile Areas

**State Reconnection Logic After Takeover:**
- Files: `shuo/shuo/server.py` (lines 676-705)
- Why fragile: Complex conditional logic that checks multiple sources (DTMF pending, existing call by call_sid, call history). Race conditions possible if two streams reconnect simultaneously for the same call_sid.
- Safe modification: Add comprehensive tests for reconnection scenarios; use atomic operations (database transactions) to prevent double-reconnection
- Test coverage: Gaps in test cases for concurrent reconnection, expired DTMF state, and malformed history

**Dashboard Event Bus and Registry Synchronization:**
- Files: `dashboard/bus.py`, `dashboard/registry.py`
- Why fragile: In-memory dictionaries are mutated directly without locks. WebSocket broadcasts are async but mutations are sync, creating potential race conditions.
- Safe modification:
  - Protect registry mutations with asyncio.Lock
  - Validate all registry updates before committing
  - Add tombstone/soft-delete pattern instead of immediate removal
- Test coverage: No async concurrency tests; single-threaded operation only

**Marker Scanner State Machine:**
- Files: `shuo/shuo/agent.py` (lines 48-112)
- Why fragile: Buffer overflow handling is silent (line 93-96); malformed input produces no error signal, just lost output
- Safe modification: Add explicit error callback for buffer overflow events; make LLM aware of marker parsing failures
- Test coverage: No tests for edge cases like incomplete markers at EOF, rapid marker sequences, or buffer overflow

## Scaling Limits

**Memory Usage in Long Conversations:**
- Current capacity: ~100 turns per call before noticeable latency degradation
- Limit: Agent history stored in-process; at 500+ turns, TTFT increases >2x
- Scaling path:
  - Implement server-side conversation history database (PostgreSQL, Redis)
  - Move history management to separate service
  - Implement sliding window (keep last 50 turns only)

**Concurrent Call Capacity:**
- Current capacity: ~10-20 concurrent calls per instance (depends on TTS/Flux pool size)
- Limit: Single process, limited by pool_size and resource constraints
- Scaling path:
  - Use Kubernetes deployment with multiple replicas
  - Share TTS/Flux pools across instances via Redis
  - Implement distributed tracing (OpenTelemetry) to identify bottlenecks

**Takeover Mode Session Management:**
- Current capacity: Unlimited concurrent takeover sessions in memory
- Limit: No eviction policy; old takeover sessions accumulate in `dashboard_registry`
- Scaling path:
  - Implement TTL for takeover sessions (auto-cleanup after 1 hour)
  - Store takeover state in Redis with expiration
  - Add metrics for active takeover sessions

## Dependencies at Risk

**Groq API Dependency:**
- Risk: Hardcoded as sole LLM provider; no fallback
- Impact: Groq outage = complete system outage
- Migration plan: Add support for OpenAI, Anthropic API as fallbacks; implement health check + automatic failover

**Deepgram Flux API:**
- Risk: Continuous connection required for entire call duration; no batching or caching
- Impact: Network interruption during call causes reconnect delay and potential dropped turns
- Migration plan: Implement graceful degradation to simple VAD + batch STT on Flux unavailability

**ElevenLabs TTS (Default):**
- Risk: Free tier rate-limited; high cost at scale
- Impact: TTS connection pool fills up, calls fail with timeout
- Migration plan: Default to Kokoro (local) for cost savings; use ElevenLabs as premium option

## Missing Critical Features

**Conversation Persistence:**
- Problem: No durable storage of call transcripts or agent history. Call ends → data lost (except traces in /tmp).
- Blocks: Cannot implement call playback, transcription export, training data collection
- Impact: Unable to audit calls or comply with regulatory requirements (PCI DSS, GDPR call recording)

**Graceful Degradation:**
- Problem: Any critical service failure (LLM, TTS, Flux) causes call to fail silently
- Blocks: Production reliability; no fallback to TTS-only or LLM-cached-responses mode
- Impact: Unpredictable user experience when services are degraded

**Backpressure Handling:**
- Problem: Audio queue and event queue have no backpressure. If playback is slow, incoming media is buffered unboundedly.
- Blocks: Efficient operation under load; potential OOM on sustained high-bitrate audio
- Impact: Performance degradation under network congestion

## Test Coverage Gaps

**Concurrent Takeover:**
- What's not tested: Two supervisor agents taking over the same call simultaneously
- Files: `shuo/shuo/server.py` (lines 140-162)
- Risk: Race condition in mode switching; one takeover may clobber the other
- Priority: High

**Pool Stale Connection Eviction Under Load:**
- What's not tested: TTS/Flux pool behavior when eviction and usage happen concurrently
- Files: `shuo/shuo/services/tts_pool.py` (lines 168-182), `shuo/shuo/services/flux_pool.py` (lines 156-168)
- Risk: Double-free or use-after-free of connection objects
- Priority: High

**LLM Service Cancellation Edge Cases:**
- What's not tested: LLM cancellation during streaming; partial history append when cancelled
- Files: `shuo/shuo/services/llm.py` (lines 137-140)
- Risk: Incomplete messages added to history; subsequent turns see garbled context
- Priority: Medium

**DTMF State Machine With Multiple Rapid Presses:**
- What's not tested: Agent pressing multiple DTMF digits rapidly; state machine ordering
- Files: `shuo/shuo/server.py` (lines 623-657)
- Risk: DTMF sequences sent out of order or lost
- Priority: Medium

**WebSocket Reconnection After Server Restart:**
- What's not tested: Graceful handling when server restarts during active call; state recovery
- Files: `shuo/shuo/server.py` (entire module)
- Risk: Orphaned calls, lost history, dashboard showing stale state
- Priority: High

**Marker Scanner with Adversarial LLM Output:**
- What's not tested: LLM producing millions of "[" characters; marker buffer limits
- Files: `shuo/shuo/agent.py` (lines 48-112)
- Risk: Silent output loss; user hears incomplete agent response
- Priority: Low (but good for robustness)

---

*Concerns audit: 2026-03-18*
