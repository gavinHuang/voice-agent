# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Install (editable):**
```bash
uv sync                  # create/update .venv
pipx install -e .        # expose voice-agent CLI globally
```

**Run tests:**
```bash
# Core tests (pure, no I/O, ~0.03s)
python -m pytest tests/ -v

# Simulator integration tests (~1s)
python -m pytest simulator/tests/ -v

# Single test
python -m pytest tests/test_agent.py::test_llm_service_streams_text_tokens -v
python -m pytest simulator/tests/test_ivr.py::test_parse_simple_config -v
```

**Test status:** 133/133 pass. 6 known warnings (all benign, do not fix):
- `websockets.legacy` deprecation — third-party library internals, not our code
- FastAPI `on_event` deprecation — `shuo/web.py` startup/shutdown hooks; migrate to `lifespan=` when convenient

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
voice-agent bench --dataset eval/scenarios/example_ivr.yaml
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
LISTENING ──UserSpokeEvent──► RESPONDING ──AgentDoneEvent──► LISTENING
    ▲                               │
    └──────UserSpeakingEvent─────────┘  (barge-in)
```

The state machine in `shuo/call.py` is the center of gravity — a pure function `step(state, event) → (state, actions)` (~30 lines). Immutable state and events make it easy to test in isolation with no I/O.

**Key modules (`shuo/`):**

| File | Role |
|------|------|
| `call.py` | Everything about a call: events, actions, state, `step()`, `run_call()` |
| `language.py` | LLM: `LanguageModel` class (Groq streaming + pydantic-ai tools) |
| `speech.py` | STT: `Transcriber` + `TranscriberPool` (Deepgram Flux) |
| `voice.py` | TTS: `VoicePool` + `AudioPlayer` + dtmf_tone() |
| `voice_elevenlabs.py` / `voice_kokoro.py` / `voice_fish.py` | TTS providers |
| `phone.py` | Phone protocol + `TwilioPhone` + `LocalPhone` + `dial_out()` |
| `agent.py` | LLM→TTS→Player pipeline per turn; translation injected here |
| `translation.py` | Bidirectional translation: `Translator` ABC, `LLMTranslator`, `DeepLTranslator`, `get_translator()` |
| `web.py` | FastAPI server (HTTP routes + WebSocket call handler) |
| `cli.py` | Click CLI |
| `bench.py` | Benchmark runner |
| `tracer.py` | Latency tracing |
| `ttft.py` | TTFT benchmark endpoint |

**Top-level directories:**

| Directory | Role |
|-----------|------|
| `shuo/` | Python package — core runtime (includes `phone.html` browser softphone) |
| `monitor/` | Supervisor UI: call registry, real-time event bus, human takeover |
| `simulator/` | YAML-driven call flow simulator for benchmarking |
| `tests/` | Test suite (133 tests) |
| `eval/` | Benchmark datasets, scenarios, and reports |
| `specs/` | API and design specs |
| `docs/` | Project documentation |
| `assets/` | Architecture diagrams |
| `scripts/` | Analysis and visualization scripts |

**Connection pooling:** Both Deepgram Flux and TTS providers maintain pre-warmed connection pools (`speech.py` `TranscriberPool`, `voice.py` `VoicePool`) to avoid cold-start latency on each turn.

**Phone abstraction:** `phone.py` defines a pluggable telephony backend. `TwilioPhone` is used in production; `LocalPhone` creates an in-process loopback for Twilio-free local development and benchmarking.

**Simulator:** The `simulator/` directory contains a YAML-configurable call flow server (flows in `simulator/flows/`). Used with `voice-agent bench` to run automated benchmarks.

## Environment Setup

Copy `.env.example` to `.env` in the repo root. Required variables:

```
TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER
TWILIO_PUBLIC_URL    # ngrok HTTPS URL in dev
TWILIO_API_KEY, TWILIO_API_SECRET  # browser softphone only
DEEPGRAM_API_KEY
GROQ_API_KEY
ELEVENLABS_API_KEY
```

Optional: `ELEVENLABS_VOICE_ID`, `LLM_MODEL`, `TTS_PROVIDER` (`elevenlabs`|`kokoro`|`fish`), `PORT` (default 3040).

**Translation (optional):** Set both `CALLER_LANG` (language the caller speaks, e.g. `Spanish`) and `CALLEE_LANG` (agent's operating language, e.g. `English`) to enable bidirectional translation. Also set `DEEPGRAM_LANGUAGE` to the caller's language code for accurate STT, and configure TTS for the caller's language (TTS speaks back in `CALLER_LANG`). `TRANSLATION_PROVIDER` selects `llm` (default, uses Groq) or `deepl` (requires `DEEPL_API_KEY`). Per-call overrides: `--caller-lang` / `--callee-lang` CLI flags.

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
