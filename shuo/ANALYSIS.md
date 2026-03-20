# shuo Voice Agent — Code Analysis

## What It Does

`shuo` is a lightweight voice agent framework (~3,700 lines) for AI-powered phone calls via Twilio. The pipeline:

```
Caller audio → Deepgram Flux (STT + turn detection) → Groq LLM (streaming) → ElevenLabs/Kokoro TTS → Twilio
```

Key features: real-time streaming, barge-in support, IVR navigation (DTMF + hold detection), conversation history, human take-over/handback, graceful shutdown, call tracing.

---

## What Makes It Good

### 1. Pure State Machine (`state.py`)
- `process_event(state, event) → (state, actions)` — all logic is side-effect-free
- Fully testable without mocks; tests run in ~0.03s
- All I/O is decoupled via action dispatch

### 2. End-to-End Streaming
- LLM tokens feed TTS immediately (no sentence buffering)
- TTS audio chunks feed Twilio in real-time
- Enables natural barge-in: caller can interrupt agent mid-sentence

```
LLM token stream → MarkerScanner → TTS.send() → on_audio() → Player.send_chunk() → Twilio WebSocket
```

### 3. Connection Pooling (`tts_pool.py`)
- TTS connections pre-warmed, reused with callback rebinding
- Reduces per-call TTS setup from ~1.4s to near-zero
- Background auto-refill with TTL eviction

### 4. Marker Protocol (`agent.py:48-112`)
- LLM embeds out-of-band control signals in text: `[DTMF:2]`, `[HOLD]`, `[HANGUP]`
- `MarkerScanner` handles token boundaries gracefully; strips markers before TTS
- Elegant solution for embedding control flow in an LLM text stream

### 5. IVR Navigation
- When navigating an IVR, incoming audio does not cancel the agent's response
- Solves a real-world problem: IVR audio was interrupting the agent before TTS flushed

IVR handling spans four interlocking mechanisms:

**DTMF key press** — LLM emits `[DTMF:2]` in its token stream; `MarkerScanner` strips it before TTS; tone is synthesized locally (two sine waves at standard telephone frequencies) and sent via Twilio REST API. DTMF-only turns are valid — no speech needed.

**Barge-in suppression** (`conversation.py:205-211`) — `ResetAgentTurnAction` is skipped when `ivr_mode()` is true, so IVR menu audio never cancels the agent mid-response.

**Hold detection** — LLM emits `[HOLD]` when placed on hold; `state.hold_mode=True` disables barge-in. Every `EndOfTurn` while on hold triggers a hold-check turn with an injected prompt asking the LLM to classify the transcript.

**Hold-check markers:**

| Marker | Meaning | Effect |
|--------|---------|--------|
| `[HOLD]` | Agent placed on hold | `hold_mode=True`, barge-in disabled |
| `[HOLD_CONTINUE]` | Still on hold music | Turn ends silently, no TTS |
| `[HOLD_END]` | Real person returned | `hold_mode=False`, normal conversation resumes |

**Full IVR flow:**

```
Call connects
    ↓
Agent: "[DTMF:1]"  (presses 1 for sales)
    ↓
IVR plays menu audio → FluxStartOfTurnEvent (suppressed — IVR mode)
    ↓
IVR: "Please hold..." → FluxEndOfTurnEvent
    ↓
LLM sees transcript → emits "[HOLD]"
    ↓
state.hold_mode = True  (barge-in now disabled)
    ↓
Hold music → repeated FluxEndOfTurnEvents
    ↓
LLM: "[HOLD_CONTINUE]"  (each time — silent turns)
    ↓
Human picks up → FluxEndOfTurnEvent with real speech
    ↓
LLM: "[HOLD_END] Hi, I'm calling about..."
    ↓
state.hold_mode = False  (normal conversation)
```

### 6. Graceful Shutdown with Draining (`server.py:621-745`)
- SIGTERM sets `_draining=True`, rejects new calls, waits for active calls to complete
- DRAIN_TIMEOUT env var prevents hanging indefinitely

### 7. Type Safety
- Frozen dataclasses for `State`, `Events`, `Actions` — clear API contracts, no implicit mutations (`types.py`)

### 8. Clean Tracing
- Per-call trace files in `/tmp/shuo/{call_id}.json`
- Records spans (tts_pool, llm, tts, player) with relative timestamps for latency analysis

---

## Potential Issues

### Concurrency / Race Conditions

| Issue | Location | Risk |
|-------|----------|------|
| `_dtmf_pending` dict written without lock | `server.py:644` | Race on concurrent call DTMF events |
| TTS pool eviction can race with `get()` | `tts_pool.py:88-102` | TOCTOU: item evicted while being dispensed |
| Token observer callback blocks LLM streaming | `agent.py:341` | Slow observer stalls entire turn |

### Error Handling Gaps

| Issue | Location | Risk |
|-------|----------|------|
| `tts.flush()` failure → `_on_done()` never called | `agent.py:368` | Conversation hangs permanently |
| `player._send_clear()` no retry on failure | `player.py:165` | Stale audio plays into next turn |
| `base64.b64decode()` unguarded | `twilio_client.py:71` | Malformed Twilio message crashes parser |
| Flux `_on_message()` silently drops parse errors | `flux.py:166` | Missed transcripts appear as hangs |

### Scalability

| Issue | Location | Risk |
|-------|----------|------|
| TTS pool size hardcoded at `2` | `server.py:86` | 3rd concurrent call blocks waiting for connection |
| No call timeout | `conversation.py` | Hung calls leak forever if Twilio never sends `StreamStopEvent` |
| Trace files accumulate unbounded | `tracer.py:26` | `/tmp/shuo/` fills disk on busy systems |
| No rate limiting on `GET /call/{phone}` | `server.py:257` | Anyone can spam calls; account bill spikes |

### Security

| Issue | Location | Risk |
|-------|----------|------|
| No Twilio request signature validation | `server.py` | Spoofed TwiML/webhook requests possible |
| API keys can appear in error log strings | `llm.py`, `server.py` | Key exposure in logs |
| Dashboard WebSocket unauthenticated | `server.py:583` | Live transcripts/tokens exposed without auth |

### LLM Configuration

| Issue | Location | Risk |
|-------|----------|------|
| Temperature hardcoded to `0.7` | `llm.py:120` | Non-deterministic behavior; flaky manual testing |
| `max_tokens=500` hardcoded | `llm.py:119` | May truncate complex responses |
| Neither value is env-configurable | — | Requires code change to tune |

### Marker Buffer Edge Case (`agent.py:90-96`)
- If LLM emits an invalid marker longer than `MAX_BUF` (20 chars), the partial buffer is flushed as literal text
- Should log a warning and discard entirely rather than emitting a partial marker as speech

### Known Limitation (Documented)
- Deepgram Flux connections **cannot be pooled** — reusing an idle connection causes the turn detector to fire prematurely on first audio
- Every call pays ~1.4s Deepgram setup cost; no known workaround yet

### Testing Gaps
- No integration tests for the full audio pipeline (Twilio → Deepgram → LLM → TTS)
- No load tests for TTS pool behavior under concurrent calls
- LLM temperature 0.7 makes unit tests non-deterministic if LLM is ever used in tests

---

## Summary

| Category | Severity |
|----------|----------|
| Missing error handling in streaming paths | High |
| Security (no request auth, no Twilio sig validation) | High |
| Global state races | Medium |
| Scalability (no timeouts, hardcoded pool size) | Medium |
| LLM config not tunable | Low |
| No integration/load tests | Low |

**Bottom line:** The architecture is well-designed — the pure state machine combined with streaming-first I/O is the right approach for low-latency voice. The main gaps are hardening: error recovery in the streaming pipeline, security at the HTTP boundary, and operational concerns (timeouts, rate limits, disk cleanup) before handling production traffic.
