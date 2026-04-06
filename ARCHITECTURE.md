# Architecture: shuo Voice Agent

> Updated: 2026-04-06
> Scope: `shuo/` — state machine, agent pipeline, call loop, server

---

## Overview

The architecture has three tiers:

1. **Pure core** (`call.py`) — immutable state, events, actions, `step()` function
2. **Service layer** (`agent.py`, `language.py`, `speech.py`, `voice.py`, `phone.py`) — streaming I/O, pooling, LLM/TTS/STT
3. **Infrastructure** (`web.py`, `monitor/`, `simulator/`) — HTTP/WS server, supervisor UI, test IVR

---

## State Machine

**Location:** `shuo/call.py` — `step(state, event) → (state, actions)`

**States:**

| Phase | Description |
|---|---|
| `LISTENING` | Waiting for user to finish speaking |
| `RESPONDING` | Agent is generating and playing response |
| `ENDING` | Call is terminating |

**Transitions:**

```
LISTENING ──UserSpokeEvent──────► RESPONDING ──AgentDoneEvent──► LISTENING
    ▲                                   │
    └──────UserSpeakingEvent────────────┘  (barge-in: cancel agent turn)
```

**Events (frozen dataclasses):**
- `CallStartedEvent`, `CallEndedEvent` — stream lifecycle
- `AudioChunkEvent` — raw μ-law audio from Twilio
- `UserSpokeEvent(transcript)` — Deepgram detected end of turn
- `UserSpeakingEvent` — Deepgram detected start of speech (barge-in trigger)
- `AgentDoneEvent` — agent finished playing response
- `HangupEvent` — agent or system requested call end
- `DTMFEvent(digit)` — DTMF digit press result

**Actions (frozen dataclasses):**
- `StreamToSTTAction(audio)` — forward audio to Transcriber
- `StartTurnAction(transcript)` — begin agent response turn
- `CancelTurnAction` — interrupt agent (barge-in or forced stop)

---

## Agent Pipeline

**Location:** `shuo/agent.py`

**Flow per turn:**
1. `StartTurnAction` → `agent.start_turn(transcript)`
2. `LanguageModel.stream(history)` → token stream via pydantic-ai
3. Clean tokens → TTS WebSocket (pool-managed via `VoicePool`)
4. TTS audio chunks → `AudioPlayer` → Twilio WebSocket
5. Tool calls (`press_dtmf`, `go_on_hold`, `signal_hangup`) handled inline
6. `AgentDoneEvent` emitted on completion

**Barge-in:** `CancelTurnAction` → cancels LLM + TTS + player tasks atomically.

---

## Connection Pooling

**VoicePool** (`shuo/voice.py`):
- Pre-warms TTS WebSocket connections (size: 2 by default)
- TTL: 120s; stale connections evicted atomically under `_lock`
- TOCTOU race fixed: evict-then-cancel pattern outside lock

**TranscriberPool** (`shuo/speech.py`):
- Deepgram connections cannot be pre-warmed (turn detector fires prematurely on reuse)
- Fresh connection per call; pool infrastructure present for future use

---

## Phone Abstraction

**Location:** `shuo/phone.py`

| Class | Use Case |
|---|---|
| `TwilioPhone` | Production — Twilio WebSocket, REST API for hangup/DTMF |
| `LocalPhone` | Testing — in-process loopback; `LocalPhone.pair()` connects two agents |

`run_call(phone, ...)` accepts any object satisfying the `Phone` protocol.

---

## Language Model

**Location:** `shuo/language.py` — `LanguageModel`

- **Framework:** pydantic-ai with Groq backend
- **Streaming:** `agent.iter()` loop over `ModelRequestNode` / `CallToolsNode`
- **Tools (typed):**
  - `press_dtmf(digit: str)` — DTMF navigation
  - `go_on_hold()` — enter hold mode (suppress barge-in)
  - `signal_hangup()` — terminate call after current speech
- No `MarkerScanner` — pydantic-ai tool calls replace the old `[DTMF:N]`/`[HANGUP]` markers

---

## Security

| Feature | Location | Mechanism |
|---|---|---|
| Twilio webhook validation | `shuo/web.py` | `verify_twilio_signature` FastAPI Depends |
| Dashboard API key auth | `monitor/server.py` | `verify_api_key` FastAPI Depends |
| Rate limiting | `monitor/server.py` | In-process `_RateLimiter` (sliding window) |
| DTMF lock | `shuo/web.py` | `_dtmf_lock: asyncio.Lock` on `_dtmf_pending` |
| VoicePool lock | `shuo/voice.py` | `_lock: asyncio.Lock` prevents double-cancel |
| Non-blocking observer | `shuo/agent.py` | `asyncio.call_soon()` for token observers |
| Inactivity watchdog | `shuo/call.py` | `_inactivity_watchdog()` — HangupEvent after timeout |
| Trace file rotation | `shuo/tracer.py` | `cleanup_traces()` runs at startup |

---

## Module Reference

| Module | Key Exports |
|---|---|
| `call.py` | `CallState`, `Phase`, `step()`, `run_call()`, all event/action types, `_inactivity_watchdog()` |
| `agent.py` | `Agent` |
| `language.py` | `LanguageModel` |
| `speech.py` | `Transcriber`, `TranscriberPool` |
| `voice.py` | `VoicePool`, `AudioPlayer`, `dtmf_tone()`, `_Entry`, `_create_tts()` |
| `voice_elevenlabs.py` | ElevenLabs TTS implementation |
| `voice_kokoro.py` | Kokoro TTS implementation |
| `voice_fish.py` | Fish Audio TTS implementation |
| `phone.py` | `Phone` (protocol), `TwilioPhone`, `LocalPhone`, `dial_out()` |
| `web.py` | `app` (FastAPI), `_dtmf_pending`, `_dtmf_lock`, `_active_calls`, `_draining` |
| `log.py` | `get_logger()`, `Logger`, `setup_logging()` |
| `tracer.py` | `Tracer`, `cleanup_traces()`, `TRACE_DIR` |
| `bench.py` | `BenchISP`, `IVRDriver`, `run_benchmark()`, `load_scenarios()` |
| `cli.py` | `main` (Click CLI entry point) |
| `ttft.py` | `router` (TTFT benchmark FastAPI router) |
