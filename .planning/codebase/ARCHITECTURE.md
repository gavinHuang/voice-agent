# Architecture

**Analysis Date:** 2026-03-18

## Pattern Overview

**Overall:** Event-driven streaming architecture with a pure state machine at its core, orchestrating three major subsystems: speech-to-text (Deepgram Flux), LLM inference (Groq), and text-to-speech (ElevenLabs/Kokoro). Real-time bidirectional audio flows through Twilio WebSockets.

**Key Characteristics:**
- Pure functional state machine (`process_event()` ~30 lines) routes all domain logic
- Immutable event/action types decouple I/O from business logic
- Streaming-first: LLM tokens feed TTS immediately; TTS audio feeds Twilio immediately
- Pre-warmed connection pools (TTS, Flux) minimize cold-start latency
- Multi-module design: `shuo` (framework), `dashboard` (monitoring), `ivr` (test server)

## Layers

**Presentation Layer (Dashboard):**
- Purpose: Real-time supervisor monitoring and call control interface
- Location: `dashboard/`
- Contains: FastAPI router, WebSocket event broadcaster, HTML UI, call registry, event bus
- Depends on: Twilio REST API, shuo's server and conversation modules
- Used by: Browser clients, supervisor operator

**Server/HTTP Layer:**
- Purpose: Accept incoming Twilio WebSocket connections, serve TwiML, expose REST endpoints
- Location: `shuo/shuo/server.py`
- Contains: FastAPI app setup, `@app.on_event("startup")` pools, endpoint handlers, token generation
- Depends on: Conversation loop, dashboard router, Twilio SDK
- Used by: Twilio voice platform, browser clients

**Conversation Loop (Event Processor):**
- Purpose: Main per-call event loop—receive events, update state, dispatch actions
- Location: `shuo/shuo/conversation.py`
- Contains: Queue management, state machine invocation, action dispatch, Flux/Agent lifecycle
- Depends on: State machine, Agent, Flux, LLM, TTS Pool
- Used by: Server (via WebSocket handler)

**State Machine (Pure Core):**
- Purpose: Deterministic (state, event) → (state, actions) transformation
- Location: `shuo/shuo/state.py`
- Contains: Phase transitions (LISTENING ↔ RESPONDING ↔ HANGING_UP), hold mode, barge-in handling
- Depends on: Type definitions only
- Used by: Conversation loop

**Agent Pipeline (Response Generator):**
- Purpose: LLM → TTS → audio streaming orchestration; owns conversation history
- Location: `shuo/shuo/agent.py`
- Contains: MarkerScanner (strips [DTMF], [HOLD], [HANGUP]), LLM invocation, TTS streaming, playback
- Depends on: LLMService, TTS pool, AudioPlayer, Tracer
- Used by: Conversation loop

**STT Service (Turn Detection):**
- Purpose: Continuous Deepgram Flux connection; detects speaker turns, generates transcripts
- Location: `shuo/shuo/services/flux.py`, `shuo/shuo/services/flux_pool.py`
- Contains: WebSocket listener to Deepgram, turn event callbacks, connection pooling
- Depends on: Deepgram SDK
- Used by: Conversation loop

**LLM Service:**
- Purpose: Streaming inference with conversation history management
- Location: `shuo/shuo/services/llm.py`
- Contains: System prompt construction, Groq streaming client, token buffering
- Depends on: OpenAI-compatible client (Groq), goal context
- Used by: Agent

**TTS Services (Multiple Providers):**
- Purpose: Text-to-speech streaming with pluggable backends
- Location: `shuo/shuo/services/tts.py` (factory), `tts_elevenlabs.py`, `tts_kokoro.py`, `tts_fish.py`, `tts_pool.py`
- Contains: Provider abstraction, ElevenLabs WebSocket streaming, local Kokoro-82M, Fish Audio S2, connection pooling
- Depends on: Provider SDKs
- Used by: Agent

**Audio Player:**
- Purpose: Async μ-law audio streaming to Twilio WebSocket
- Location: `shuo/shuo/services/player.py`
- Contains: Queue-based frame buffering, Twilio media frame formatting
- Depends on: Twilio SDK
- Used by: Agent

**IVR Engine (Standalone Server):**
- Purpose: YAML-driven mock phone system for testing; renders TwiML
- Location: `ivr/config.py`, `ivr/engine.py`, `ivr/server.py`, `ivr/flows/`
- Contains: Config parser (YAML), TwiML renderer, node types (say, menu, softphone, pause, hangup)
- Depends on: FastAPI, Twilio SDK
- Used by: Testing agent's DTMF navigation

## Data Flow

**Inbound (Phone → Agent Response):**

1. **Twilio WebSocket** → `server.py` `/ws` handler
2. **Twilio message parser** (`twilio_client.parse_twilio_message()`) → MediaEvent or stream control
3. **Event queue** (per-call asyncio.Queue)
4. **State machine** checks phase; if LISTENING, routes MediaEvent → FeedFluxAction
5. **Flux service** accumulates audio, detects turn end → FluxEndOfTurnEvent
6. **State machine** transitions LISTENING → RESPONDING, emits StartAgentTurnAction
7. **Agent** receives transcript, invokes LLM

**Agent Response (LLM → Playback):**

1. **LLMService** streams tokens from Groq (OpenAI-compatible)
2. **MarkerScanner** strips markers ([DTMF:N], [HOLD], [HANGUP]) in real time
3. **Clean text** feeds **TTS service** (pool-managed WebSocket)
4. **TTS chunks** accumulate as audio frames
5. **AudioPlayer** sends frames → Twilio WebSocket in real time
6. **Agent turn complete** → AgentTurnDoneEvent
7. **State machine** transitions RESPONDING → LISTENING

**Barge-In (User Interruption):**

1. **Flux** detects FluxStartOfTurnEvent (user started speaking)
2. **State machine** (if RESPONDING and not hold_mode) → ResetAgentTurnAction
3. **Agent** cancels LLM + TTS + player tasks immediately
4. **Twilio buffer** is flushed
5. **Loop returns to LISTENING**

**State Preservation (Takeover/Handback):**

1. **Dashboard POST /calls/{id}/takeover** → `should_suppress_agent(True)`, agent yields
2. **Agent** saves history, stops playback, marks mode as TAKEOVER
3. **Supervisor** speaks → recorded via listen-only stream
4. **Dashboard POST /calls/{id}/handback** → agent resumes with preserved history

**State Management:**

- **AppState** (immutable, `types.py`): phase, stream_sid, hold_mode only
- **Agent state** (mutable): conversation history (list of dicts), current LLM/TTS/player tasks
- **Registry** (`dashboard/registry.py`): per-call metadata for UI (phone, goal, mode, agent ref)
- **Event bus** (`dashboard/bus.py`): per-call queue + global queue for dashboard broadcasts

## Key Abstractions

**Event (Union Type):**
- Purpose: Immutable input to state machine
- Examples: `MediaEvent`, `FluxEndOfTurnEvent`, `FluxStartOfTurnEvent`, `AgentTurnDoneEvent`, `HangupRequestEvent`, `DTMFToneEvent`
- Pattern: Dataclasses with frozen=True; typed union discriminates in `process_event()`

**Action (Union Type):**
- Purpose: Immutable output from state machine
- Examples: `FeedFluxAction`, `StartAgentTurnAction`, `ResetAgentTurnAction`
- Pattern: Matched by `isinstance()` in dispatch loop

**Phase Enum:**
- Purpose: Router for state machine decisions
- Values: LISTENING (waiting for user), RESPONDING (agent active), HANGING_UP (call ending)

**MarkerScanner:**
- Purpose: Online (streaming) marker detection and stripping
- Files: `shuo/shuo/agent.py`
- Pattern: Stateful token-by-token processor; buffers partial markers across boundaries

**FluxService / TTSPool / LLMService:**
- Purpose: Streaming I/O abstractions with async/await
- Pattern: Constructor takes callbacks; `start()` / `stop()` lifecycle; bind() for pool rebinding

## Entry Points

**Phone Call (Inbound via Twilio):**
- Location: `shuo/shuo/server.py` route `@app.post("/twiml")` or `@app.post("/twiml/ivr-dtmf")`
- Triggers: Twilio dials the public URL (via ngrok or deployment)
- Responsibilities: Return TwiML with WebSocket URL, custom parameters (phone, call_sid)

**WebSocket Stream:**
- Location: `shuo/shuo/server.py` route `@app.websocket("/ws")`
- Triggers: Twilio connects to `/ws` endpoint from TwiML
- Responsibilities: Accept WebSocket, invoke `run_conversation_over_twilio()`, manage graceful shutdown

**Dashboard Control:**
- Location: `dashboard/server.py` routes `/dashboard/*`
- Triggers: Browser opens `/dashboard`, clicks Place Call / Takeover / Handback
- Responsibilities: Call placement via Twilio REST, event broadcasting, call lifecycle

**Outbound Call (Script):**
- Location: `shuo/main.py` or `make_call.py`
- Triggers: CLI execution with phone number
- Responsibilities: Invoke `make_outbound_call()` via Twilio REST, wait for WebSocket connect

## Error Handling

**Strategy:** Graceful degradation; errors logged but not breaking the loop. Conversation continues on recoverable errors (STT latency, LLM failures).

**Patterns:**

- **Flux errors** (`flux.py`): Reconnect on disconnect; log warning, continue listening
- **LLM errors** (`llm.py`): Timeout fallback (repeat last transcript or emit silence); log and retry
- **TTS errors** (`tts_*.py`): Pool returns dead connection; replacement pulled from pool or new one created
- **Agent cancellation** (`agent.py`): asyncio.CancelledError caught; cleanup tasks, clear history as needed
- **Twilio disconnect** (`conversation.py`): StreamStopEvent ends loop cleanly; final cleanup in try/finally

**Recovery:**
- TTS/Flux pools designed to auto-recover via TTL and lazy reconstruction
- Agent tasks are cancellable; no orphaned tasks
- WebSocket handler wraps everything in try/finally for cleanup

## Cross-Cutting Concerns

**Logging:**
- Centralized in `shuo/shuo/log.py`; colored ANSI output for CLI, ServiceLogger per module
- Pattern: `log = ServiceLogger("ModuleName")` → `log.info(msg)`, `log.debug(msg)`
- Used for debugging latency, task transitions, error conditions

**Validation:**
- Minimal; trust Twilio/Deepgram/Groq SDKs for correctness
- Only explicit validation: Flux transcript non-empty before StartAgentTurnAction, DTMF marker format in MarkerScanner

**Authentication:**
- Twilio API keys in `.env` (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_API_KEY)
- Deepgram, Groq, ElevenLabs API keys in `.env`
- No request authentication; assumes private deployment or ngrok URL protection

**Latency Tracing:**
- Tracer class (`tracer.py`) records per-turn spans (LLM first token, TTS first audio, playback complete)
- Output: JSON to `/tmp/shuo/{stream_sid}.json`
- Endpoint: `GET /trace/latest` returns most recent trace

---

*Architecture analysis: 2026-03-18*
