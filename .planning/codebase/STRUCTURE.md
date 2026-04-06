# Codebase Structure

**Analysis Date:** 2026-04-06 (updated after greenfield module refactor + directory restructure)

## Directory Layout

```
voice-agent/
├── shuo/                    # Core voice agent framework (Python package)
│   ├── __init__.py
│   ├── call.py              # Events, actions, CallState, step(), run_call()
│   ├── agent.py             # LLM→TTS→Player pipeline; owns history
│   ├── language.py          # LanguageModel (Groq streaming + pydantic-ai tools)
│   ├── speech.py            # Transcriber + TranscriberPool (Deepgram Flux)
│   ├── voice.py             # VoicePool + AudioPlayer + dtmf_tone()
│   ├── voice_elevenlabs.py  # ElevenLabs WebSocket streaming TTS
│   ├── voice_kokoro.py      # Local Kokoro-82M TTS
│   ├── voice_fish.py        # Fish Audio S2 self-hosted TTS
│   ├── phone.py             # TwilioPhone + LocalPhone + dial_out()
│   ├── web.py               # FastAPI app, endpoints, pool warmup
│   ├── cli.py               # Click CLI (serve, call, bench, local-call, …)
│   ├── bench.py             # IVR benchmark runner
│   ├── tracer.py            # Per-turn latency instrumentation
│   ├── log.py               # Colored logging
│   └── ttft.py              # TTFT benchmark endpoint
├── monitor/                 # Supervisor monitoring + call control
│   ├── __init__.py
│   ├── server.py            # FastAPI router (all /dashboard/* routes)
│   ├── registry.py          # Active call store (call_id → metadata)
│   ├── bus.py               # Event bus (per-call + global queues)
│   └── app.html             # Supervisor UI (served from /dashboard)
├── simulator/               # YAML-driven mock phone system (test server)
│   ├── __init__.py
│   ├── main.py              # Uvicorn entry point
│   ├── server.py            # FastAPI app, TwiML endpoints
│   ├── config.py            # YAML parser for IVR flows
│   ├── engine.py            # TwiML renderer for nodes
│   ├── flows/               # YAML flow definitions
│   │   └── example.yaml     # Sample IVR call flow
│   └── tests/               # Integration tests (no network)
│       ├── __init__.py
│       ├── conftest.py      # Pytest fixtures
│       └── test_ivr.py      # Config, routing, TwiML generation tests
├── ui/                      # Browser softphone (static)
│   └── phone.html           # WebRTC client for Twilio SDK
├── eval/                    # Benchmark data, scenarios, and reports
│   ├── data/                # Benchmark datasets (ABCD, MultiWOZ, TauBench)
│   ├── scenarios/           # YAML scenario files for voice-agent bench
│   └── reports/             # Benchmark run results
├── tests/                   # Core test suite (133 tests)
│   ├── __init__.py
│   ├── conftest.py          # Adds project root to sys.path
│   ├── test_update.py       # State machine and event handling tests
│   ├── test_agent.py        # Agent pipeline tests
│   ├── test_bench.py        # Benchmark runner tests
│   ├── test_bug_fixes.py    # Race condition and watchdog tests
│   ├── test_cli.py          # CLI command tests
│   ├── test_dashboard_auth.py  # Dashboard auth + rate limiting tests
│   ├── test_isp.py          # Phone abstraction tests
│   ├── test_ivr_barge_in.py # IVR barge-in suppression tests
│   ├── test_regression.py   # End-to-end regression tests
│   └── test_webhook_security.py  # Twilio signature + trace rotation tests
├── docs/                    # Project documentation
├── specs/                   # API and design specs (openspec format)
├── assets/                  # Architecture diagrams
├── scripts/                 # Analysis and visualization scripts
├── .planning/               # GSD planning documents
│   └── codebase/            # Architecture, structure, conventions, testing docs
├── main.py                  # Entry point: start server, optional outbound call
├── pyproject.toml           # Package config + CLI entry point
├── .env.example             # Environment variable template
├── make_call.py             # One-shot CLI script: dial number
├── hangup_all.py            # Utility: terminate all Twilio calls
└── restart.sh               # Dev script: kill :3040 and restart
```

## Directory Purposes

**shuo/:**
- Purpose: Core voice agent framework — all logic for handling calls, STT, LLM, TTS, state management
- Contains: Pure state machine, streaming I/O services, conversation loop, agent pipeline
- Key files: `call.py` (state machine + event loop), `agent.py` (response pipeline), `web.py` (HTTP/WS server)

**monitor/:**
- Purpose: Real-time supervisor interface for monitoring and controlling live calls
- Contains: FastAPI router, event broadcasting, call registry, HTML UI
- Key files: `server.py` (endpoints), `registry.py` (state), `bus.py` (event pub/sub), `app.html` (UI)

**simulator/:**
- Purpose: Test/mock server simulating an automated phone system (IVR) via TwiML
- Contains: Config loader (YAML), TwiML engine, node type handlers, browser softphone token generation
- Used for: Local testing of agent's DTMF navigation before integrating with real IVRs

**ui/:**
- Purpose: Browser-based Twilio WebRTC client for answering calls and supervisor takeover
- Contains: Static HTML + JavaScript using Twilio SDK
- Pattern: Fetch access token from server (`/token` or `/ivr/token`), register with Twilio, handle calls

**tests/:**
- Purpose: Core unit and integration tests for the shuo package
- Contains: 133 tests covering state machine, agent pipeline, CLI, security, benchmarks
- Run with: `python -m pytest tests/ -v`

**eval/:**
- Purpose: Benchmark infrastructure — datasets, scenario YAML files, and run reports
- data/: Raw benchmark datasets (ABCD, MultiWOZ, TauBench airline/retail)
- scenarios/: YAML files consumed by `voice-agent bench`
- reports/: JSON + Markdown reports from benchmark runs

**.planning/codebase/:**
- Purpose: GSD codebase mapping documents (generated by `/gsd:map-codebase`)
- Contains: ARCHITECTURE.md, STRUCTURE.md, CONVENTIONS.md, TESTING.md, CONCERNS.md
- Used by: `/gsd:plan-phase` and `/gsd:execute-phase` to understand codebase patterns

## Key File Locations

**Entry Points:**
- `main.py`: Start server; optionally make outbound call (CLI or direct)
- `shuo/web.py`: FastAPI app (started by uvicorn in production or main.py)
- `simulator/main.py`: IVR mock server entry point
- `make_call.py`: One-off Twilio REST call (no shuo server needed)

**Configuration:**
- `.env`: Environment variables (Twilio keys, Deepgram key, Groq key, ElevenLabs key, etc.)
- `pyproject.toml`: Python package config + `voice-agent` CLI entry point
- `simulator/flows/example.yaml`: IVR flow definition (nodes, routing, prompts)

**Core Logic:**
- `shuo/call.py`: Pure state machine `step()` + event loop `run_call()` (LISTENING/RESPONDING/ENDING)
- `shuo/agent.py`: Agent response pipeline (LanguageModel + VoicePool + AudioPlayer)
- `shuo/language.py`: LanguageModel with pydantic-ai tools (DTMF, hold, hangup)

**Testing:**
- `tests/`: Unit tests for state machine, agent, CLI, security, benchmarks
- `simulator/tests/`: Integration tests for IVR simulator (no network access)

**Tracing & Debugging:**
- `shuo/tracer.py`: Per-call latency instrumentation
- `shuo/log.py`: Centralized logging with colors
- Output: `/tmp/shuo/{stream_sid}.json` (latency trace), console (colored logs)

## Naming Conventions

**Files:**
- Snake_case: `voice_elevenlabs.py`, `voice_kokoro.py`
- Domain-named modules at top of package: `call.py`, `speech.py`, `voice.py`, `phone.py`
- Test files: `test_*.py` (pytest convention)

**Directories:**
- Lowercase domain names: `monitor/`, `simulator/`, `ui/`, `eval/`
- No `services/` subfolder — all modules are flat in `shuo/`

**Classes:**
- PascalCase: `CallState`, `Agent`, `LanguageModel`, `Transcriber`, `VoicePool`, `TwilioPhone`, `LocalPhone`
- Enums: `Phase`, `CallMode` (singular names for state/mode)

**Functions & Methods:**
- Lowercase with underscores: `step()`, `run_call()`, `dial_out()`, `render_node()`
- Private (internal): `_ms_since()`, `_evict_stale()`, `_on_llm_token()`
- Async: `async def run_call()`, `async def start()`

**Constants:**
- ALL_CAPS: `CALL_INACTIVITY_TIMEOUT`, `DRAIN_TIMEOUT`
- Enum variants: `Phase.LISTENING`, `Phase.RESPONDING`, `Phase.ENDING`

## Where to Add New Code

**New Feature (E2E):**
- Framework changes: `shuo/call.py` (new events/actions), `shuo/agent.py`, `shuo/web.py`
- New TTS provider: `shuo/voice_{name}.py` + update `_create_tts()` factory in `shuo/voice.py`
- Monitor feature: `monitor/server.py` (new endpoint) + `monitor/app.html` (UI)
- Tests: `tests/test_{feature}.py` or `simulator/tests/test_{feature}.py`

**New Streaming Provider (TTS/STT):**
- TTS: `shuo/voice_{name}.py` + register in `shuo/voice.py:_create_tts()`
- STT: Extend `shuo/speech.py`
- Test: Add unit test to `tests/`

**New IVR Node Type:**
- Location: `simulator/config.py` (add node class), `simulator/engine.py` (add render method)
- Tests: Add case to `simulator/tests/test_ivr.py`

**Monitor Control (e.g., new button):**
- Endpoint: `monitor/server.py` new `@router.post("/calls/{id}/new_action")`
- Event: Broadcast via `dashboard_bus.publish_global(event)` in call loop
- UI: Edit `monitor/app.html` to add button and WebSocket handler

**New Utility/Script:**
- Location: `scripts/` (analysis scripts) or root level (one-off utilities)
- Examples: `make_call.py`, `hangup_all.py`, `scripts/visualize.py`

## Special Directories

**shuo/__pycache__/, monitor/__pycache__/, simulator/__pycache__/:**
- Purpose: Python bytecode caches
- Generated: Yes (automatically by Python)
- Committed: No (listed in .gitignore)

**shuo/.venv/, shuo/.venv-kokoro/:**
- Purpose: Python virtual environments (pipx-managed and Kokoro-specific)
- Generated: Yes
- Committed: No

**.pytest_cache/:**
- Purpose: Pytest cache and test artifact storage
- Generated: Yes
- Committed: No

**/tmp/shuo/:**
- Purpose: Runtime latency traces (JSON files per call)
- Generated: Yes (by Tracer during calls)
- Location: System temp directory; persists across restarts
- Rotation: Automatic via `cleanup_traces()` on server startup

---

*Structure analysis updated: 2026-04-06*
