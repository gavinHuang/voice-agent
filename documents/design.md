# System Design: Voice Agent Platform

**Status:** Target architecture — describes where the system should go, not where it is today.

---

## 1. Layered Architecture

The system separates concerns into three distinct layers. Today these are entangled; the design goal is to make each independently replaceable.

```
┌─────────────────────────────────────────────────────────┐
│  Agent Layer  (intelligence, goals, identity, roles)     │
│  VoiceAgent | IVRAgent | HumanAgent                      │
├─────────────────────────────────────────────────────────┤
│  Voice Layer  (audio pipeline, protocol-agnostic)        │
│  STT | TTS | TurnDetection | DTMF | EmotionDetection     │
├─────────────────────────────────────────────────────────┤
│  ISP Layer  (network / telephony transport)              │
│  TwilioISP | LocalISP | WebRTCISP                        │
└─────────────────────────────────────────────────────────┘
```

### ISP Layer

Owns the carrier-level connection. Produces/consumes raw audio streams and exposes a uniform interface upward regardless of transport.

```python
class ISP(Protocol):
    async def send_audio(self, audio: bytes) -> None: ...
    async def recv_audio(self) -> AsyncIterator[bytes]: ...
    async def send_dtmf(self, digit: str) -> None: ...
    async def hangup(self) -> None: ...
    async def call(self, phone: str) -> "Session": ...
```

**Implementations:**

| Implementation | Status | Notes |
|---|---|---|
| `TwilioISP` | ✅ Exists (`twilio_client.py`, `server.py`) | μ-law 8kHz WebSocket |
| `LocalISP` | ❌ Missing | Loopback for local testing without Twilio |
| `WebRTCISP` | ❌ Missing | Browser-to-browser (Twilio SDK softphone is partial) |

**Gap:** `LocalISP` is the most critical missing piece. Without it, every test requires a real phone number and Twilio credentials, which blocks fast iteration and IVR benchmarking.

### Voice Layer

Stateless audio processing. Takes raw audio bytes from ISP, returns typed events. All providers are swappable.

```python
class VoiceSession:
    stt: STTProvider          # Deepgram Flux / Whisper
    tts: TTSProvider          # ElevenLabs / Kokoro / Fish
    turn_detector: TurnDetector
    dtmf_gen: DTMFGenerator
    emotion: EmotionDetector  # future
```

**Status:** ✅ Most exists in `shuo/services/`. The issue is tight coupling to Twilio-specific formats (μ-law 8kHz). Abstracting audio format negotiation between layers is needed.

### Agent Layer

Pure intelligence — no I/O, no audio processing. Consumes turn-level text events, returns text + control signals.

```python
class Agent(Protocol):
    async def on_turn(self, transcript: str, ctx: ConversationContext) -> AgentResponse: ...
```

`AgentResponse` can include:
- `speech: str` — text to speak
- `dtmf: list[str]` — digits to press (IVR navigation)
- `action: Literal["hold", "hangup", "transfer"]`
- `tool_calls: list[ToolCall]` — future: extensible tool use

**Implementations:**

| Implementation | Status |
|---|---|
| `LLMAgent` | ✅ Exists (`agent.py`), but tightly coupled to TTS/pipeline |
| `IVRAgent` | ✅ Partial — IVR module is separate, not using Agent protocol |
| `HumanAgent` | ❌ Missing — softphone exists but not abstracted as an Agent |
| `EchoAgent` | ❌ Missing — needed for benchmarks |

---

## 2. Roles in a Call

A call session connects exactly two roles. Either role can be any implementation:

```
┌──────────────┐          ┌──────────────┐
│  Side A       │◄────────►│  Side B       │
│  (caller)     │  Session │  (callee)     │
└──────────────┘          └──────────────┘

Role choices per side:
- LLMAgent      → LLM-powered conversational agent
- IVRAgent      → pre-scripted YAML-driven menu
- HumanAgent    → softphone UI (microphone + speaker)
- EchoAgent     → benchmarking stub (returns scripted responses)
```

**Today:** the caller/callee distinction is implicit and hard-coded. The system always assumes the agent is the caller and the callee is a human or IVR. Modeling both sides explicitly enables local call testing (LLMAgent ↔ IVRAgent without Twilio).

---

## 3. Applications

### 3.1 CLI (`cli/`)

**Status: ❌ Missing**

The CLI should be the primary way to launch and compose system components. It enables use as a pluggable skill in external agent systems (e.g., OpenClaw).

```bash
# Start the backend server + ngrok tunnel
voice-agent serve --port 8000 --ngrok

# Make an outbound call
voice-agent call +15551234567 \
  --goal "Navigate IVR and check account balance" \
  --identity "You are Alex, a customer service researcher"

# Run IVR benchmark
voice-agent bench --dataset benchmarks/sample.yaml \
  --isp local \
  --report results/

# Start a local test call (no Twilio)
voice-agent local-call \
  --agent-a config/agent.yaml \
  --agent-b flows/example.yaml

# Start softphone
voice-agent softphone --listen
```

Design principles:
- Each subcommand maps 1:1 to a use case from the wish doc
- `--isp local` flag swaps ISP layer to LocalISP (no Twilio needed)
- All config expressible as YAML files, CLI flags are overrides
- Outputs structured JSON logs for scripting

### 3.2 IVR System

**Status: ✅ Mostly exists (`ivr/`)**

A YAML-configured mock IVR that acts as a phone service. Gaps:

- ❌ No `local` ISP mode — always requires a Twilio number
- ❌ Softphone operator routing is incomplete
- ❌ No SSML/audio file support (only text-to-speech)

### 3.3 Softphone

**Status: ⚠️ Partially exists (`softphone/`, `client/`)**

Browser-based phone that lets a human participate. Gaps:

- ❌ Not modeled as `HumanAgent` implementing the Agent protocol
- ❌ Two redundant implementations (`softphone/` and `client/`) — should be one
- ❌ No local mode (requires Twilio for audio)
- ❌ No call initiation UI — only receives calls

### 3.4 Monitoring Dashboard

**Status: ✅ Mostly exists (`dashboard/`)**

Live transcript view, takeover, handback, hangup. Gaps:

- ❌ No authentication — any browser can access and control calls
- ❌ No rate limiting on `/call` endpoint
- ❌ Twilio webhook signatures not validated (security)
- ❌ Trace files accumulate unbounded in `/tmp/shuo/`

---

## 4. Key Component Designs

### 4.1 LocalISP

The highest-leverage missing component. Enables:
- Unit-testable end-to-end call flows
- IVR benchmark runs without Twilio
- CI/CD pipeline testing

```
AgentA                     AgentB
  │                           │
  │──── LocalISP ────────────►│
  │   (in-process loopback)   │
  │◄──────────────────────────│
```

Implementation: two `asyncio.Queue` instances bridged. Audio written by A is readable by B, and vice versa. Same interface as `TwilioISP`.

### 4.2 IVR Benchmark

**Status: ❌ Missing**

A dataset + runner to evaluate how reliably the LLM agent navigates IVR systems.

```yaml
# benchmarks/sample.yaml
scenarios:
  - id: balance_inquiry
    description: "Navigate 3-level menu to check account balance"
    isp: local
    agent_a: configs/default_agent.yaml       # the agent being tested
    agent_b: flows/example.yaml               # the IVR being navigated
    success_criteria:
      - transcript_contains: "Your balance is"
      - dtmf_sequence: ["1", "2", "1"]        # expected key presses
    timeout_seconds: 60
```

Metrics:
- Success rate per scenario
- Average turns to completion
- DTMF accuracy (pressed vs expected)
- Wall-clock latency

### 4.3 Agent Framework

**Status: ⚠️ Custom implementation exists; no formal framework**

The current `agent.py` is purpose-built and couples LLM, TTS, and marker scanning together. The desire is a pluggable agent framework.

Candidate: **pydantic-ai** (mentioned in wish doc as "pydantic")

Benefits:
- Typed tool definitions
- Structured output validation
- Multi-model support
- `run_sync` / `run` / streaming modes already match the turn-based model

Migration path:
1. Extract `LLMAgent` logic into a `pydantic_ai.Agent` with tools for `[DTMF]`, `[HOLD]`, `[HANGUP]`
2. Replace marker scanning with structured agent output (`AgentResponse` dataclass)
3. Keep `agent.py` as a thin adapter (Voice Layer → pydantic-ai → Action types)

### 4.4 Emotion Detection

**Status: ❌ Missing (mentioned in wish doc)**

Low priority but architecturally: lives in the Voice Layer as an optional `EmotionDetector` that produces `EmotionEvent` alongside `TranscriptEvent`. Could be attached to the dashboard for supervisor insight.

---

## 5. Gap Summary

| Component | Status | Priority | Effort |
|---|---|---|---|
| `LocalISP` (loopback) | ❌ | High | Medium |
| CLI tool | ❌ | High | Medium |
| IVR Benchmark dataset + runner | ❌ | High | Medium |
| Layer abstraction (ISP/Voice/Agent protocols) | ⚠️ Entangled | High | Large |
| Security (auth, rate limiting, Twilio sig) | ❌ | High | Small |
| `HumanAgent` protocol wrapper | ❌ | Medium | Small |
| Agent framework (pydantic-ai migration) | ⚠️ Custom | Medium | Large |
| Softphone consolidation | ⚠️ Duplicated | Low | Small |
| Emotion detection | ❌ | Low | Large |
| Known race conditions (DTMF lock, pool TOCTOU) | 🐛 | Medium | Small |
| Trace file cleanup | 🐛 | Low | Small |

---

## 6. Recommended Build Order

**Phase 1 — Local testing foundation**
1. Define `ISP` protocol interface
2. Implement `LocalISP` (in-process loopback)
3. Wire `VoiceSession` to accept any `ISP`

**Phase 2 — CLI**
1. `voice-agent serve` (wraps current `main.py`)
2. `voice-agent call` (wraps `make_call.py`)
3. `voice-agent local-call` (uses LocalISP, no Twilio)

**Phase 3 — IVR Benchmark**
1. YAML benchmark schema
2. Scenario runner (spawns agent ↔ IVR in local mode)
3. Metrics report

**Phase 4 — Agent framework**
1. Migrate `LLMAgent` to pydantic-ai
2. Define `AgentResponse` structured type (replaces marker scanning)

**Phase 5 — Security & hardening**
1. Dashboard authentication
2. Twilio webhook signature validation
3. Rate limiting on `/call`
4. Trace file rotation

**Phase 6 — Observability & emotion**
1. Structured trace export (replace raw JSON)
2. Emotion detection (Voice Layer plugin)
