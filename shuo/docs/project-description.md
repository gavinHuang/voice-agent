# Shuo — Voice Agent: Project Description

> Reference document for rebuilding or extending this system.
> Source: https://github.com/NickTikhonov/shuo

---

## 1. What It Does

Shuo is a real-time voice conversation agent delivered over a phone call. A caller dials a Twilio number; the system listens, understands what the caller said, generates an LLM reply, speaks it back with synthesized voice, and repeats. The whole cycle targets **~400 ms end-to-end latency** from end-of-user-speech to first audio out.

Both inbound (caller dials in) and outbound (server initiates the call) modes are supported.

---

## 2. High-Level Pipeline

```
Caller
  │  (PSTN)
  ▼
Twilio ──── WebSocket (8 kHz μ-law audio) ────► FastAPI /ws
                                                      │
                                              ┌───────┴────────┐
                                              │  Conversation   │
                                              │  Event Loop     │
                                              └───────┬────────┘
                                                      │ audio bytes
                                                      ▼
                                              Deepgram Flux v2
                                              (STT + turn detection)
                                                      │ transcript
                                                      ▼
                                                  Groq LLM
                                              (llama-3.3-70b)
                                                streaming tokens
                                                      │
                                                      ▼
                                              ElevenLabs TTS
                                              (eleven_turbo_v2_5,
                                               ulaw_8000 output)
                                                base64 audio chunks
                                                      │
                                                      ▼
                                           AudioPlayer → Twilio WS
                                                      │
                                                  (PSTN)
                                                      ▼
                                                   Caller
```

Every stage streams to the next without buffering — LLM tokens flow directly into TTS as they arrive, and TTS audio chunks flow directly to the player as they arrive.

---

## 3. Core Architectural Principles

### 3.1 Pure State Machine

The conversation controller (`state.py`) is a pure function:

```
process_event(state: AppState, event: Event) -> (AppState, List[Action])
```

It has **no side effects**. All I/O (sending audio, cancelling tasks) is done by the caller (`conversation.py`) after inspecting the returned actions. This makes the state logic trivially testable in pure unit tests.

### 3.2 Event / Action Types

**Events** (inputs into the system):
| Event | Source | Meaning |
|---|---|---|
| `StreamStartEvent` | Twilio | WebSocket connection established, stream SID available |
| `StreamStopEvent` | Twilio | Call ended |
| `MediaEvent` | Twilio | Raw audio chunk (μ-law bytes) |
| `FluxStartOfTurnEvent` | Deepgram Flux | User started speaking (barge-in signal) |
| `FluxEndOfTurnEvent` | Deepgram Flux | User finished speaking; includes transcript |
| `AgentTurnDoneEvent` | AudioPlayer | Agent finished speaking |

**Actions** (outputs from the state machine):
| Action | Handler | Effect |
|---|---|---|
| `FeedFluxAction` | `flux.send()` | Send audio bytes to Deepgram |
| `StartAgentTurnAction` | `agent.start_turn()` | Start LLM → TTS → Player pipeline |
| `ResetAgentTurnAction` | `agent.cancel_turn()` | Cancel pipeline, clear Twilio buffer |

### 3.3 Two-Phase State

```python
class Phase(Enum):
    LISTENING   # Waiting for / receiving user speech
    RESPONDING  # Agent pipeline active (LLM → TTS → Player)
```

```python
class AppState:
    phase: Phase        # Current conversation phase
    stream_sid: str     # Twilio stream identifier
```

All conversation history lives in `Agent._llm.history`, not in `AppState`. State only carries routing information.

### 3.4 Barge-In (Interruption)

When `FluxStartOfTurnEvent` arrives while `phase == RESPONDING`, the state machine emits `ResetAgentTurnAction`. The agent cancels LLM generation, closes the TTS WebSocket, stops the audio player, and sends a Twilio `clear` message to flush buffered audio from the phone network. History is preserved — the interrupted partial response is recorded with `"..."` appended.

---

## 4. File Structure

```
shuo/
├── main.py                     # Entry point: server startup, SIGTERM handling, outbound call trigger
├── requirements.txt
├── .env.example
├── Procfile                    # Railway: web: uvicorn shuo.server:app --host 0.0.0.0 --port $PORT
├── docs/
│   ├── project-description.md  # This document
│   └── api-plan.md             # Plan for the secure configurable call API (not yet implemented)
├── scripts/
│   ├── bench_chart.py          # Renders TTFT benchmark chart from JSON
│   ├── visualize.py            # Renders latency span waterfall from trace JSON
│   └── service_map.py          # Generates service_map.png diagram
├── tests/
│   └── test_update.py          # Pure unit tests for process_event (no I/O)
└── shuo/
    ├── types.py                # Immutable events, actions, state (dataclasses + enums)
    ├── state.py                # Pure state machine: process_event()
    ├── conversation.py         # Async event loop: receives events, dispatches actions
    ├── agent.py                # LLM → TTS → Player pipeline per turn; owns conversation history
    ├── log.py                  # Colored console logging (Logger, ServiceLogger, ColorFormatter)
    ├── server.py               # FastAPI app: /health /twiml /ws /trace/latest /bench/ttft
    ├── tracer.py               # Per-call latency span recorder (saves to /tmp/shuo/<call_id>.json)
    └── services/
        ├── flux.py             # Deepgram Flux v2 STT + turn detection (always-on WS)
        ├── llm.py              # Groq/OpenAI streaming LLM; manages conversation history
        ├── tts.py              # ElevenLabs streaming TTS over WebSocket
        ├── tts_pool.py         # Pre-warmed TTS connection pool with TTL eviction
        ├── player.py           # Audio drip loop → Twilio WebSocket (~20 ms chunks)
        └── twilio_client.py    # Outbound call creation; Twilio message parsing
```

---

## 5. Component Details

### 5.1 `main.py` — Entry Point

- Loads `.env`, validates required environment variables.
- Starts uvicorn in a daemon thread (port from `PORT` env var, default 3040).
- Optionally initiates an outbound call via CLI argument (`python main.py +1234567890`).
- Handles `SIGTERM` with graceful drain: stops accepting new calls, waits up to `DRAIN_TIMEOUT` seconds (default 300) for active calls to finish.

### 5.2 `server.py` — FastAPI Application

**Endpoints:**

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Returns `{"status": "ok"}` — no auth |
| `GET/POST` | `/twiml` | Returns TwiML XML to connect a media stream; rejects calls during drain |
| `WS` | `/ws` | Twilio media stream; tracks active call count for graceful shutdown |
| `GET` | `/trace/latest` | Returns most recent call trace JSON from `/tmp/shuo/` |
| `GET` | `/call/{phone_number}` | Triggers an outbound call (no auth — legacy endpoint) |
| `GET` | `/bench/ttft` | TTFT benchmark across OpenAI/Groq models (30 runs each, randomised schedule) |

The WebSocket endpoint increments/decrements a global `_active_calls` counter and checks `_draining` for graceful shutdown signalling.

### 5.3 `conversation.py` — Main Event Loop

Single function `run_conversation_over_twilio(websocket)` that manages one call end-to-end:

1. Creates shared `asyncio.Queue[Event]`.
2. Creates `FluxService` with callbacks that push events to the queue.
3. Starts a `read_twilio()` background task that reads Twilio JSON frames and pushes typed events.
4. On `StreamStartEvent`: starts Flux, starts TTSPool, creates the `Agent`.
5. Main loop: `event = await queue.get()` → `process_event()` → dispatch actions.
6. Cleanup on exit: cancels reader, cleans up agent, stops pool and Flux, saves trace.

### 5.4 `agent.py` — Response Pipeline

Owns the per-call response pipeline and conversation history.

**Lifecycle per turn:**
1. `start_turn(transcript)` — acquires TTS connection from pool, creates AudioPlayer, starts LLM.
2. `_on_llm_token(token)` — pipes each token directly to TTS.
3. `_on_llm_done()` — flushes TTS.
4. `_on_tts_audio(audio_base64)` — pipes each audio chunk to AudioPlayer.
5. `_on_tts_done()` — marks AudioPlayer that no more chunks are coming.
6. `_on_playback_done()` — fires `AgentTurnDoneEvent` back into the event queue.

`cancel_turn()` cancels in dependency order: LLM → TTS → Player → Twilio clear.

Latency milestones are recorded: `tts_pool`, `llm_first_token`, `tts_first_audio`, total turn duration.

### 5.5 `services/flux.py` — Speech-to-Text + Turn Detection

Uses **Deepgram Flux v2** (`flux-general-en` model) via a persistent WebSocket connection:
- Endpoint: `wss://api.eu.deepgram.com` (EU routing for lower latency from EU deployment).
- Audio format: μ-law 8 kHz (direct from Twilio, no conversion needed).
- Receives `TurnInfo` events: `StartOfTurn` (barge-in) and `EndOfTurn` (transcript ready).
- Also optionally handles interim `Results` events for streaming partial transcripts.
- Connection lives for the entire call duration (always-on, not per-turn).

Replaces the earlier architecture of local VAD (Silero) + separate STT.

### 5.6 `services/llm.py` — Language Model

- Uses Groq's OpenAI-compatible API (`https://api.groq.com/openai/v1`).
- Default model: `llama-3.3-70b-versatile` (configurable via `LLM_MODEL` env var).
- Maintains `_history: List[Dict]` across turns (system prompt prepended per request).
- Streams tokens via `on_token` callback; calls `on_done` when complete.
- On cancellation: appends partial response with `"..."` to history.
- System prompt (hardcoded): instructs the agent to be concise and conversational, avoiding markdown/formatting unsuitable for speech.

### 5.7 `services/tts.py` — Text-to-Speech

- Uses **ElevenLabs** streaming WebSocket API.
- Model: `eleven_turbo_v2_5`, output format: `ulaw_8000` (direct Twilio compatible).
- Default voice: `21m00Tcm4TlvDq8ikWAM` (Rachel), configurable via `ELEVENLABS_VOICE_ID`.
- Text is sent token-by-token as LLM produces it (`try_trigger_generation: True`).
- Final flush via `{"text": "", "flush": True}` at LLM completion.
- `bind(on_audio, on_done)` allows callback reassignment without reconnecting (used by pool).

### 5.8 `services/tts_pool.py` — Connection Pool

Pre-warms ElevenLabs WebSocket connections to eliminate the ~200–300 ms connection setup time per turn.

- Pool size: 1 (one pre-connected idle connection).
- TTL: 8 seconds (ElevenLabs closes idle connections after ~10 s).
- On `get()`: if a warm, non-stale connection exists, rebind callbacks and return it; otherwise connect fresh.
- Background `_fill_loop`: after dispensing, immediately starts a new connection. Also periodically evicts stale entries.
- Idle connections use no-op callbacks until dispensed.

### 5.9 `services/player.py` — Audio Drip Loop

Streams audio chunks to Twilio at a rate that mimics real-time playback (~20 ms per chunk).

- Accepts chunks via `send_chunk(base64_audio)` as they arrive from TTS.
- Runs an independent asyncio loop: sends one chunk, sleeps 20 ms, repeats.
- `mark_tts_done()` signals no more chunks; loop exits after sending remaining buffer.
- `stop_and_clear()`: cancels loop, sends Twilio `clear` event to flush network buffer.

### 5.10 `services/twilio_client.py` — Twilio Integration

- `make_outbound_call(to)`: uses Twilio REST API to place a call with TwiML URL pointing to `/twiml`; uses `edge="frankfurt"` for EU routing.
- `parse_twilio_message(data)`: converts raw Twilio WebSocket JSON into typed events:
  - `"connected"` → log only, return `None`
  - `"start"` → `StreamStartEvent(stream_sid=...)`
  - `"media"` → `MediaEvent(audio_bytes=base64.b64decode(payload))`
  - `"stop"` → `StreamStopEvent()`

### 5.11 `tracer.py` — Latency Tracer

Records named time spans and point-in-time markers for each agent turn.

- `begin_turn(transcript)` → returns turn number.
- `begin(turn, name)` / `end(turn, name)` → records span duration.
- `mark(turn, name)` → records a timestamp.
- `cancel_turn(turn)` → closes all open spans at cancellation time.
- `save(call_id)` → writes JSON to `/tmp/shuo/<call_id>.json`.

Spans recorded per turn: `tts_pool`, `llm`, `tts`, `player`.
Markers: `llm_first_token`, `tts_first_audio`.

The `scripts/visualize.py` script renders a waterfall chart from trace files.

### 5.12 `log.py` — Logging

- `setup_logging()`: configures a single colored console handler with millisecond timestamps.
- `Logger` (class): class-method lifecycle events (server start, call initiate, WebSocket connect/disconnect); instance-method event/action/transition logging in the conversation loop.
- `ServiceLogger`: per-service (Flux, LLM, TTS, Player, Agent) with distinct colors.
- High-frequency `MediaEvent` and `FeedFluxAction` are suppressed by default (verbose=False).

---

## 6. State Machine Transition Table

| Current Phase | Event | New Phase | Actions Emitted |
|---|---|---|---|
| any | `StreamStartEvent` | LISTENING | — |
| RESPONDING | `StreamStopEvent` | RESPONDING | `ResetAgentTurnAction` |
| LISTENING | `StreamStopEvent` | LISTENING | — |
| any | `MediaEvent` | unchanged | `FeedFluxAction` |
| LISTENING | `FluxEndOfTurnEvent` (non-empty) | RESPONDING | `StartAgentTurnAction` |
| RESPONDING | `FluxEndOfTurnEvent` | RESPONDING | — (ignored) |
| LISTENING | `FluxEndOfTurnEvent` (empty) | LISTENING | — |
| RESPONDING | `FluxStartOfTurnEvent` | LISTENING | `ResetAgentTurnAction` |
| LISTENING | `FluxStartOfTurnEvent` | LISTENING | — |
| RESPONDING | `AgentTurnDoneEvent` | LISTENING | — |
| LISTENING | `AgentTurnDoneEvent` | LISTENING | — |

---

## 7. Latency Optimization Techniques

| Technique | Savings | Details |
|---|---|---|
| TTS connection pool | ~200–300 ms | Pre-warms ElevenLabs WS before user finishes speaking |
| Token streaming (LLM → TTS) | ~100–200 ms | TTS begins synthesizing before LLM finishes |
| Deepgram Flux turn detection | ~50–100 ms | Replaces multi-step VAD → STT pipeline |
| EU-region endpoints | ~50–100 ms | Deepgram EU, Twilio frankfurt edge, ElevenLabs Netherlands |
| Groq LLM | ~100–200 ms | Very fast TTFT for llama-3.3-70b |
| Twilio μ-law passthrough | ~0 ms | Audio sent directly, no codec conversion |

Target end-to-end latency: **~400 ms** (EU deployment, measured from end-of-user-speech to first audio byte played).

---

## 8. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TWILIO_ACCOUNT_SID` | Yes | — | Twilio account identifier |
| `TWILIO_AUTH_TOKEN` | Yes | — | Twilio authentication token |
| `TWILIO_PHONE_NUMBER` | Yes | — | Twilio number (E.164 format) |
| `TWILIO_PUBLIC_URL` | Yes | — | Public HTTPS URL of this server (for TwiML webhook) |
| `DEEPGRAM_API_KEY` | Yes | — | Deepgram API key (Flux access required) |
| `GROQ_API_KEY` | Yes | — | Groq API key |
| `OPENAI_API_KEY` | Yes | — | OpenAI API key (used by benchmark endpoint) |
| `ELEVENLABS_API_KEY` | Yes | — | ElevenLabs API key |
| `ELEVENLABS_VOICE_ID` | No | `21m00Tcm4TlvDq8ikWAM` | ElevenLabs voice ID |
| `LLM_MODEL` | No | `llama-3.3-70b-versatile` | Groq model name |
| `PORT` | No | `3040` | HTTP server port |
| `DRAIN_TIMEOUT` | No | `300` | Seconds to wait for active calls before forced shutdown |

---

## 9. Running Locally

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in API keys and TWILIO_PUBLIC_URL

# Inbound only (wait for calls)
python main.py

# Outbound call
python main.py +1234567890
```

The server must be publicly reachable at `TWILIO_PUBLIC_URL` for Twilio webhooks. Use ngrok or a Railway deployment for local development.

---

## 10. Deployment

Deployed on **Railway** EU region. `Procfile`:
```
web: uvicorn shuo.server:app --host 0.0.0.0 --port $PORT
```

Key deployment notes:
- `/tmp/shuo/` is used for trace files (Linux path, not available on Windows).
- `DRAIN_TIMEOUT` gives Railway's SIGTERM a chance to finish active calls before the container dies.
- Twilio is configured with `edge="frankfurt"` for EU routing.

---

## 11. Testing

`tests/test_update.py` contains pure unit tests for the state machine with no I/O or mocking. Tests cover:
- Stream lifecycle (start, stop).
- Media routing (always feeds Flux regardless of phase).
- Flux end-of-turn (starts agent, ignores empty transcripts, ignores if already responding).
- Flux start-of-turn (barge-in when responding, no-op when listening).
- Agent turn done (transitions to LISTENING).
- Complete multi-turn flows.
- Barge-in followed by new turn.
- State immutability.

```bash
python -m pytest tests/ -v
```

---

## 12. Planned Extension: Configurable Call API

See `docs/api-plan.md` for a detailed plan. Key additions not yet implemented:

### New Endpoints
```
POST   /v1/calls           # Launch a call with per-call config (auth required)
GET    /v1/calls/{call_id} # Get call status (auth required)
DELETE /v1/calls/{call_id} # Hang up (auth required)
```

### Per-Call Configuration (POST body)
```json
{
  "phone_number": "+1234567890",
  "system_prompt": "You are...",
  "first_message": "Hello!",
  "llm": { "model": "llama-3.3-70b-versatile", "provider": "groq", "temperature": 0.7, "max_tokens": 500 },
  "voice": { "voice_id": "abc123", "stability": 0.5, "similarity_boost": 0.75 },
  "max_duration": 300,
  "recording": true
}
```

### Architecture Changes Required
- `models.py`: Pydantic request models + `CallConfig` frozen dataclass.
- `auth.py`: Bearer token auth via `API_KEYS` env var (comma-separated).
- `call_registry.py`: In-memory `call_sid → CallConfig` registry (no database).
- `types.py`: Add `call_sid` field to `StreamStartEvent`.
- `llm.py` / `tts.py` / `tts_pool.py`: Accept config params instead of hardcoded env reads.
- `agent.py`: Accept `CallConfig`, add `send_first_message()`.
- `conversation.py`: Look up config from registry on stream start.

### Config Flow
```
POST /v1/calls
  → CallConfig validated
  → make_outbound_call() → call_sid
  → registry.register(call_sid, config)
  → Twilio calls /twiml → /ws
  → StreamStartEvent.call_sid → registry.get_config()
  → Agent(config) → LLMService + TTSPool with per-call params
```

---

## 13. Key Design Decisions Summary

| Decision | Rationale |
|---|---|
| Pure state machine | Testable without mocks; clear separation of logic and I/O |
| Flux for VAD + STT | Eliminates multi-step pipeline; purpose-built for voice turn detection |
| TTS connection pool | Biggest latency win; pre-connects before the user finishes speaking |
| Pipelining LLM → TTS | No wait for full LLM response before starting TTS |
| μ-law 8 kHz throughout | Matches Twilio's native format; zero transcoding overhead |
| asyncio throughout | Single-threaded async handles all I/O without thread overhead |
| No database | Call configs are transient; registry is in-memory per-process |
| EU-region everything | Co-locate all services to minimize cross-region RTT |
