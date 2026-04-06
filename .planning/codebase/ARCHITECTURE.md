# Architecture

**Analysis Date:** 2026-04-06 (updated after greenfield module refactor)

## Pattern Overview

**Overall:** Event-driven streaming architecture with a pure state machine at its core, orchestrating three major subsystems: speech-to-text (Deepgram Flux), LLM inference (Groq + pydantic-ai), and text-to-speech (ElevenLabs/Kokoro). Real-time bidirectional audio flows through Twilio WebSockets.

**Key Characteristics:**
- Pure functional state machine (`step()` ~30 lines in `call.py`) routes all domain logic
- Immutable event/action types decouple I/O from business logic
- Streaming-first: LLM tokens feed TTS immediately; TTS audio feeds Twilio immediately
- Pre-warmed connection pools (VoicePool, TranscriberPool) minimize cold-start latency
- pydantic-ai typed tool calls replace custom text markers for DTMF/hold/hangup
- Multi-module design: `shuo` (framework), `monitor` (monitoring), `simulator` (test server)

## Layers

**Presentation Layer (Monitor):**
- Purpose: Real-time supervisor monitoring and call control interface
- Location: `monitor/`
- Contains: FastAPI router, WebSocket event broadcaster, HTML UI, call registry, event bus
- Depends on: Twilio REST API, shuo's web and call modules
- Used by: Browser clients, supervisor operator

**Server/HTTP Layer:**
- Purpose: Accept incoming Twilio WebSocket connections, serve TwiML, expose REST endpoints
- Location: `shuo/web.py`
- Contains: FastAPI app setup, `@app.on_event("startup")` pools, endpoint handlers, token generation
- Depends on: Call loop, monitor router, Twilio SDK

**Call Loop (Event Processor):**
- Purpose: Main per-call event loop — receive events, update state, dispatch actions
- Location: `shuo/call.py` (`run_call()`)
- Contains: Queue management, state machine invocation, action dispatch, Transcriber/Agent lifecycle
- Depends on: State machine (`step()`), Agent, Transcriber, VoicePool

**State Machine (Pure Core):**
- Purpose: Deterministic `(state, event) → (state, actions)` transformation
- Location: `shuo/call.py` (`step()`)
- Contains: Phase transitions (LISTENING ↔ RESPONDING ↔ ENDING), hold mode, barge-in handling
- Depends on: Type definitions only

**Agent Pipeline (Response Generator):**
- Purpose: LanguageModel → TTS → audio streaming orchestration; owns conversation history
- Location: `shuo/agent.py`
- Contains: LanguageModel invocation, TTS streaming, playback, pydantic-ai tool handling
- Depends on: LanguageModel, VoicePool, AudioPlayer, Tracer

**STT Service (Turn Detection):**
- Purpose: Continuous Deepgram Flux connection; detects speaker turns, generates transcripts
- Location: `shuo/speech.py` (`Transcriber`, `TranscriberPool`)
- Contains: WebSocket listener to Deepgram, turn event callbacks, connection pooling
- Depends on: Deepgram SDK

**Language Model:**
- Purpose: Streaming inference with conversation history management and typed tool calls
- Location: `shuo/language.py` (`LanguageModel`)
- Contains: System prompt construction, Groq streaming client, pydantic-ai tool definitions
- Depends on: pydantic-ai, Groq (OpenAI-compatible)
- Tools: `press_dtmf()`, `go_on_hold()`, `signal_hangup()`

**TTS Services (Multiple Providers):**
- Purpose: Text-to-speech streaming with pluggable backends
- Location: `shuo/voice.py` (factory + VoicePool + AudioPlayer), `voice_elevenlabs.py`, `voice_kokoro.py`, `voice_fish.py`
- Contains: Provider abstraction, ElevenLabs WebSocket streaming, local Kokoro-82M, Fish Audio S2, connection pooling
- Depends on: Provider SDKs

**Phone Abstraction:**
- Purpose: Pluggable telephony backend (Twilio production or LocalPhone for in-process testing)
- Location: `shuo/phone.py`
- Contains: `TwilioPhone`, `LocalPhone`, `dial_out()`
- Pattern: `LocalPhone.pair()` connects two in-process agents without Twilio

**IVR Engine (Standalone Server):**
- Purpose: YAML-driven mock phone system for testing; renders TwiML
- Location: `simulator/config.py`, `simulator/engine.py`, `simulator/server.py`, `simulator/flows/`
- Contains: Config parser (YAML), TwiML renderer, node types (say, menu, softphone, pause, hangup)
- Depends on: FastAPI, Twilio SDK

## Data Flow

**Inbound (Phone → Agent Response):**

1. **Twilio WebSocket** → `web.py` `/ws` handler
2. **TwilioPhone.read()** parses messages → MediaEvent or stream control
3. **Event queue** (per-call asyncio.Queue)
4. **`step()`** checks phase; if LISTENING, routes AudioChunkEvent → StreamToSTTAction
5. **Transcriber** accumulates audio, detects turn end → UserSpokeEvent
6. **`step()`** transitions LISTENING → RESPONDING, emits StartTurnAction
7. **Agent** receives transcript, invokes LanguageModel

**Agent Response (LLM → Playback):**

1. **LanguageModel** streams tokens from Groq via pydantic-ai
2. **Clean text** feeds TTS service (pool-managed WebSocket)
3. **TTS chunks** accumulate as audio frames
4. **AudioPlayer** sends frames → Twilio WebSocket in real time
5. **Agent turn complete** → AgentDoneEvent
6. **`step()`** transitions RESPONDING → LISTENING

**Barge-In (User Interruption):**

1. **Transcriber** detects UserSpeakingEvent (user started speaking)
2. **`step()`** (if RESPONDING and not hold_mode) → CancelTurnAction
3. **Agent** cancels LanguageModel + TTS + player tasks immediately
4. **Twilio buffer** is flushed
5. **Loop returns to LISTENING**

**State Preservation (Takeover/Handback):**

1. **Monitor POST /calls/{id}/takeover** → `should_suppress_agent(True)`, agent yields
2. **Agent** saves history, stops playback, marks mode as TAKEOVER
3. **Supervisor** speaks → recorded via listen-only stream
4. **Monitor POST /calls/{id}/handback** → agent resumes with preserved history

**State Management:**

- **CallState** (immutable, `call.py`): phase, hold_mode only
- **Agent state** (mutable): conversation history (pydantic-ai ModelMessage list), current LLM/TTS/player tasks
- **Registry** (`monitor/registry.py`): per-call metadata for UI (phone, goal, mode, agent ref)
- **Event bus** (`monitor/bus.py`): per-call queue + global queue for monitor broadcasts

## Key Abstractions

**Event (Union Type):**
- Purpose: Immutable input to state machine
- Examples: `AudioChunkEvent`, `UserSpokeEvent`, `UserSpeakingEvent`, `AgentDoneEvent`, `HangupEvent`, `DTMFEvent`
- Pattern: Dataclasses with frozen=True; typed union discriminates in `step()`

**Action (Union Type):**
- Purpose: Immutable output from state machine
- Examples: `StreamToSTTAction`, `StartTurnAction`, `CancelTurnAction`
- Pattern: Matched by `isinstance()` in dispatch loop

**Phase Enum:**
- Purpose: Router for state machine decisions
- Values: LISTENING (waiting for user), RESPONDING (agent active), ENDING (call ending)

**Transcriber / TranscriberPool:**
- Purpose: Deepgram Flux connection with async/await lifecycle
- Location: `shuo/speech.py`
- Pattern: Constructor takes callbacks; `start()` / `stop()` lifecycle; pool pre-warms connections

**VoicePool / AudioPlayer:**
- Purpose: TTS connection pooling and audio streaming to Twilio
- Location: `shuo/voice.py`
- Pattern: Pool manages warm TTS connections; AudioPlayer queues μ-law frames to WebSocket

**TwilioPhone / LocalPhone:**
- Purpose: Pluggable telephony backend
- Location: `shuo/phone.py`
- Pattern: `LocalPhone.pair()` for in-process testing without Twilio

## Entry Points

**Phone Call (Inbound via Twilio):**
- Location: `shuo/web.py` route `@app.post("/twiml")`
- Triggers: Twilio dials the public URL (via ngrok or deployment)

**WebSocket Stream:**
- Location: `shuo/web.py` route `@app.websocket("/ws")`
- Triggers: Twilio connects to `/ws` endpoint from TwiML
- Responsibilities: Accept WebSocket, invoke `run_call(TwilioPhone(...))`, manage graceful shutdown

**Monitor Control:**
- Location: `monitor/server.py` routes `/dashboard/*`
- Triggers: Browser opens `/dashboard`, clicks Place Call / Takeover / Handback

**Outbound Call (Script/CLI):**
- Location: `main.py` or `voice-agent call`
- Responsibilities: Invoke `dial_out()` via Twilio REST, wait for WebSocket connect

## Error Handling

**Strategy:** Graceful degradation; errors logged but not breaking the loop.

**Patterns:**
- **Transcriber errors** (`speech.py`): Reconnect on disconnect; log warning, continue listening
- **LanguageModel errors** (`language.py`): Timeout fallback; log and retry
- **TTS errors** (`voice.py`): Pool returns dead connection; replacement pulled from pool or new one created
- **Agent cancellation** (`agent.py`): asyncio.CancelledError caught; cleanup tasks
- **Twilio disconnect** (`call.py`): StreamStopEvent ends loop cleanly; final cleanup in try/finally

## Cross-Cutting Concerns

**Logging:**
- Centralized in `shuo/log.py`; colored ANSI output for CLI, Logger per module
- Pattern: `logger = get_logger("shuo.module")` → `logger.info(msg)`

**Authentication:**
- Twilio webhook signature validation: `verify_twilio_signature` FastAPI dependency in `web.py`
- Dashboard API key auth: `verify_api_key` FastAPI dependency in `monitor/server.py`
- Rate limiting: In-process `_RateLimiter` in `monitor/server.py`

**Latency Tracing:**
- Tracer class (`tracer.py`) records per-turn spans
- Output: JSON to `/tmp/shuo/{stream_sid}.json`
- Endpoint: `GET /trace/latest` returns most recent trace
- Rotation: `cleanup_traces()` runs at startup (configurable via `TRACE_MAX_FILES`, `TRACE_MAX_AGE_HOURS`)

---

*Architecture analysis updated: 2026-04-06*
