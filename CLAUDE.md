# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Install (editable):**
```bash
pipx install -e ./shuo
```

**Run tests:**
```bash
# Core shuo tests (pure, no I/O, ~0.03s)
cd shuo && python -m pytest tests/ -v

# IVR integration tests (~1s)
python -m pytest ivr/tests/ -v

# Single test
python -m pytest shuo/tests/test_agent.py::test_llm_service_streams_text_tokens -v
python -m pytest ivr/tests/test_ivr.py::test_parse_simple_config -v
```

**Development shortcuts (via `run.sh`):**
```bash
./run.sh serve              # Agent server with auto ngrok
./run.sh all                # Agent + IVR together
./run.sh call +61400000000  # Outbound call
./run.sh local-call         # Two agents locally (no Twilio)
./run.sh bench              # IVR benchmark
./run.sh config             # Show masked env config
./run.sh stop               # Kill servers
```

**CLI (`voice-agent` command after install):**
```bash
voice-agent serve [--ngrok] [--port N]
voice-agent call <phone> [--goal "..."] [--ngrok]
voice-agent ivr-serve [--port N]
voice-agent local-call [--caller-goal "..."]
voice-agent bench --dataset scenarios/example.yaml
```

## Architecture

This is a real-time AI voice agent platform targeting ~400ms end-to-end latency. It enables LLM-powered agents to make/receive phone calls and navigate IVR systems, with a supervisor dashboard for monitoring and human takeover.

**Audio pipeline:**
```
Caller ──(PSTN)──► Twilio ──(WebSocket μ-law 8kHz)──► FastAPI /ws
                                                           │
                                                   Deepgram Flux (STT)
                                                           │ transcript
                                                       Groq LLM
                                                   (llama-3.3-70b)
                                                           │ tokens
                                                       ElevenLabs TTS
                                                           │ audio
                                                    Twilio WebSocket
                                                           │
                                                      Caller hears
```

**State machine (pure functional core):**
```
LISTENING ──FluxEndOfTurn──► RESPONDING ──AgentTurnDone──► LISTENING
    ▲                              │
    └──────FluxStartOfTurn─────────┘  (barge-in)
```

The state machine in `shuo/shuo/state.py` is the center of gravity — a pure function `process_event(state, event) → (state, actions)` (~30 lines). Immutable state and events make it easy to test in isolation with no I/O.

**Key modules:**

| File | Role |
|------|------|
| `shuo/shuo/types.py` | Frozen dataclasses: `State`, all `Event` types, all `Action` types |
| `shuo/shuo/state.py` | Pure state machine — the only place state transitions live |
| `shuo/shuo/conversation.py` | Async event loop: receives events, calls `process_event`, dispatches actions |
| `shuo/shuo/agent.py` | LLM → TTS → Player pipeline; owns conversation history; emits `AgentTurnDone` |
| `shuo/shuo/server.py` | FastAPI server; Twilio WebSocket `/ws`; all HTTP routes |
| `shuo/shuo/cli.py` | Click CLI wiring (`serve`, `call`, `bench`, etc.) |
| `shuo/shuo/services/flux.py` | Deepgram Flux WebSocket client (STT + turn detection) |
| `shuo/shuo/services/llm.py` | Groq streaming with pydantic-ai tools (DTMF, hold, hangup) |
| `shuo/shuo/services/tts_*.py` | TTS providers: ElevenLabs (primary), Kokoro (local), Fish Audio |
| `shuo/shuo/services/player.py` | Streams TTS audio back to Twilio WebSocket |
| `shuo/shuo/services/flux_pool.py` | Pre-warmed Deepgram connection pool |
| `shuo/shuo/services/tts_pool.py` | Pre-warmed TTS connection pool |
| `shuo/shuo/services/isp.py` | Abstract telephony backend interface |
| `shuo/shuo/services/twilio_isp.py` | Twilio implementation of ISP |
| `shuo/shuo/services/local_isp.py` | In-process loopback (no Twilio, for `local-call`) |
| `dashboard/` | Supervisor UI: call registry, real-time event bus, human takeover |
| `ivr/` | YAML-driven IVR mock server for testing agent call flows |
| `softphone/phone.html` | Browser WebRTC softphone via Twilio SDK |

**Connection pooling:** Both Deepgram Flux and TTS providers maintain pre-warmed connection pools (`flux_pool.py`, `tts_pool.py`) to avoid cold-start latency on each turn.

**ISP abstraction:** `isp.py` defines a pluggable telephony backend. `TwilioISP` is used in production; `LocalISP` creates an in-process loopback for Twilio-free local development and benchmarking.

**IVR mock server:** The `ivr/` directory contains a YAML-configurable IVR server (flows in `ivr/flows/`). Used with `voice-agent bench` to run automated benchmarks against scripted call flows.

## Environment Setup

Copy `shuo/.env.example` to `shuo/.env`. Required variables:

```
TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER
TWILIO_PUBLIC_URL    # ngrok HTTPS URL in dev
TWILIO_API_KEY, TWILIO_API_SECRET  # browser softphone only
DEEPGRAM_API_KEY
GROQ_API_KEY
ELEVENLABS_API_KEY
```

Optional: `ELEVENLABS_VOICE_ID`, `LLM_MODEL`, `TTS_PROVIDER` (`elevenlabs`|`kokoro`|`fish`), `PORT` (default 3040).

## Key Server Endpoints

| Path | Description |
|------|-------------|
| `/ws` | Twilio media stream WebSocket |
| `/twiml` | Twilio webhook entry (returns TwiML) |
| `/dashboard` | Supervisor dashboard UI |
| `/phone` | Browser softphone |
| `/token` | Twilio Access Token |
| `/trace/latest` | Last call latency trace (JSON) |
| `/health` | Health check |
