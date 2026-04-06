# voice-agent

A real-time AI voice agent that makes outbound phone calls and lets you monitor and take over live. Built on [shuo](shuo/README.md) — a ~600-line Python voice agent framework.

The agent listens with Deepgram Flux, replies with Groq LLaMA 3.3, and speaks with ElevenLabs (or a local Kokoro model) — targeting ~400 ms end-to-end latency. When the goal is accomplished it says a short goodbye and hangs up automatically.

---

## Repo layout

```
voice-agent/
  shuo/               # Core voice agent framework (Python package)
  monitor/            # Supervisor dashboard (UI, call registry, event bus)
  simulator/          # IVR mock server (config-driven, YAML call flows)
  ui/                 # Browser softphone (static, no server required)
  eval/               # Benchmark datasets, scenarios, and reports
  tests/              # Test suite (133 tests)
  scripts/            # Analysis and visualization scripts
  docs/               # Project documentation
  specs/              # API and design specs
  assets/             # Architecture diagrams
  run.sh              # Dev shortcuts — see Quick Reference below
  make_call.py        # One-shot outbound call script
  hangup_all.py       # Utility to terminate all active Twilio calls
```

---

## Quick Reference

### Install

```bash
pipx install -e .   # installs voice-agent globally (editable)
```

### `run.sh` — common workflows

```bash
./run.sh serve                       # agent server + auto ngrok tunnel
./run.sh ivr                         # IVR mock server + bind cloud ngrok endpoint
./run.sh all                         # agent + IVR together (Ctrl+C stops all)
./run.sh softphone                   # start server + open browser softphone
./run.sh call +61400000000           # outbound call (uses CALL_GOAL from .env)
./run.sh call +61400000000 "Book an appointment for Monday"
./run.sh local-call                  # two LLM agents talk locally — no Twilio
./run.sh bench                       # run IVR benchmark (eval/scenarios/example_ivr.yaml)
./run.sh config                      # show all config (API keys masked)
./run.sh stop                        # kill agent + IVR servers by port
./run.sh logs                        # tail agent log
./run.sh logs ivr                    # tail IVR log
```

### `voice-agent` CLI

```bash
voice-agent serve [--ngrok] [--port N]
voice-agent call <phone> [--goal "..."] [--identity "..."] [--ngrok]
voice-agent ivr-serve [--port N] [--ngrok] [--ivr-config flows/my.yaml]
voice-agent softphone [--ngrok] [--no-browser]
voice-agent local-call [--caller-goal "..."] [--callee-goal "..."]
voice-agent bench --dataset eval/scenarios/example_ivr.yaml
voice-agent config
```

### Key URLs (once server is running)

| URL | Description |
|---|---|
| `/dashboard` | Supervisor dashboard — place calls, monitor, take over |
| `/phone` | Browser softphone — answer / speak as human |
| `/ivr-mock/twiml` | IVR entry point (when IVR is mounted on agent server) |
| `/trace/latest` | Latest call latency trace (JSON) |
| `/health` | Health check |

### ngrok endpoints

| Service | URL |
|---|---|
| Agent server | set by `--ngrok` → `TWILIO_PUBLIC_URL` |
| IVR mock server | `https://jessi-foxlike-brielle.ngrok-free.dev` (static cloud endpoint) |

Twilio webhook for IVR phone number → `https://jessi-foxlike-brielle.ngrok-free.dev/twiml`

---

## Prerequisites

- Python 3.9+
- [ngrok](https://ngrok.com/) (for local development — Twilio needs a public HTTPS URL)
- API keys for: **Twilio**, **Deepgram**, **Groq**, **ElevenLabs**

---

## Setup

```bash
# Install the CLI globally
pipx install -e .

# Copy and fill in your keys
cp .env.example .env

# Verify config
./run.sh config
```

The `--ngrok` flag (or `run.sh serve/ivr/all`) starts ngrok automatically and sets `TWILIO_PUBLIC_URL` — no manual tunnel management needed.

---

## Configuration

All config lives in `.env`:

| Variable | Required | Description |
|---|---|---|
| `TWILIO_ACCOUNT_SID` | Yes | From [Twilio Console](https://console.twilio.com) |
| `TWILIO_AUTH_TOKEN` | Yes | From Twilio Console |
| `TWILIO_PHONE_NUMBER` | Yes | Your Twilio number in E.164 format, e.g. `+61400000000` |
| `TWILIO_PUBLIC_URL` | Yes | Public HTTPS URL of this server (ngrok URL for local dev) |
| `TWILIO_API_KEY` | Yes | Twilio API Key SID (needed for browser softphone) |
| `TWILIO_API_SECRET` | Yes | Twilio API Key Secret (needed for browser softphone) |
| `DEEPGRAM_API_KEY` | Yes | From [Deepgram Console](https://console.deepgram.com) |
| `GROQ_API_KEY` | Yes | From [Groq Console](https://console.groq.com) |
| `ELEVENLABS_API_KEY` | Yes | From [ElevenLabs](https://elevenlabs.io) |
| `ELEVENLABS_VOICE_ID` | No | Voice ID (default: Rachel `21m00Tcm4TlvDq8ikWAM`) |
| `LLM_MODEL` | No | Groq model (default: `llama-3.3-70b-versatile`) |
| `TTS_PROVIDER` | No | TTS engine: `elevenlabs` (default), `kokoro`, or `fish` |
| `PORT` | No | Server port (default: `3040`) |
| `DRAIN_TIMEOUT` | No | Seconds to wait for active calls on SIGTERM (default: `300`) |

> **Note:** `CALL_GOAL` and `INITIAL_MESSAGE` are managed through the dashboard UI — no need to set them in `.env`.

To create a Twilio API Key (needed for the browser softphone):
Twilio Console → Account → API Keys → Create new key → copy SID and Secret.

---

## Running

```bash
./run.sh serve        # agent only
./run.sh all          # agent + IVR mock
```

Then open the dashboard at `https://your-ngrok-url/dashboard`.

---

## How it works

```
Phone / Browser
     │  (PSTN or WebRTC)
     ▼
Twilio ── WebSocket (μ-law 8kHz audio) ──► FastAPI /ws
                                                │
                                         Deepgram Flux
                                       (STT + turn detection)
                                                │ transcript
                                                ▼
                                            Groq LLM
                                       (llama-3.3-70b streaming)
                                                │ tokens
                                                ▼
                                          ElevenLabs TTS
                                       (streaming, ulaw_8000)
                                                │ audio
                                                ▼
                                         Twilio WebSocket
                                                │
                                            Caller hears
```

Everything streams end-to-end. LLM tokens feed TTS immediately; TTS audio feeds Twilio immediately. Barge-in cancels the agent pipeline instantly. When the agent's tool call signals hangup after completing its goal, the call is terminated via the Twilio REST API.

See [shuo/README.md](shuo/README.md) for the framework internals — state machine, architecture diagram, and project structure.

---

## Dashboard

The supervisor dashboard lives at `GET /dashboard` and is served by the `monitor/` module.

### Placing a call

Fill in the form at the top of the dashboard:

- **Phone** — E.164 number (e.g. `+61400000000`), or `client:browser` to call the browser softphone
- **Goal** — what the agent should accomplish (e.g. `Confirm the appointment for tomorrow at 2pm`)
- **IVR Mode** — enable when calling an automated phone system (see [IVR navigation](#ivr-navigation))

Click **📞 Place Call**. The agent dials out, introduces itself, and works toward the goal.

### Monitoring calls

Each active call shows:

- Phone number, goal, and elapsed time
- Live transcript — caller on the left, agent on the right
- Phase badge: `LISTENING` / `RESPONDING`

### Taking over

To step in manually:

1. Open `https://your-ngrok-url/phone` in another tab (browser softphone)
2. Click **Register**
3. In the dashboard, click **🎤 Take Over** — the agent goes silent; a 3-way conference connects you directly to the caller
4. Speak to the caller; what you say is transcribed in real time
5. Click **🔙 Hand Back** to return control to the agent (it picks up where it left off)

### Summarizing outcomes

When a call ends the panel stays open with an **OUTCOME** section. Click **✨ Summarize** to generate a one-sentence LLM summary.

### Dashboard API

| Method | Path | Description |
|---|---|---|
| `GET` | `/dashboard` | Supervisor dashboard UI |
| `WS` | `/dashboard/ws` | Live event stream (one subscription per browser tab) |
| `GET` | `/dashboard/calls` | List active calls as JSON |
| `POST` | `/dashboard/call` | Place call `{phone, goal, ivr_mode}` |
| `POST` | `/dashboard/calls/{id}/hangup` | Terminate call |
| `POST` | `/dashboard/calls/{id}/takeover` | Suppress agent; connect human supervisor |
| `POST` | `/dashboard/calls/{id}/handback` | Return control to agent |
| `POST` | `/dashboard/calls/{id}/dtmf` | Inject a DTMF digit `{digit}` |
| `POST` | `/dashboard/summarize` | Generate call summary `{transcript}` |

---

## IVR navigation

The agent can navigate automated phone menus (IVR / interactive voice response) by sending DTMF key presses via the Twilio REST API.

### How it works

1. Set **IVR Mode** in the dashboard when placing a call — suppresses the opening greeting so the agent listens first
2. The LLM calls the `press_dtmf` tool with the digit to press
3. The server redirects the call to `/twiml/ivr-dtmf?digit=2`, which sends the real DTMF tone to the remote party via `<Play digits="2"/>`
4. The stream reconnects, the agent's conversation history is preserved, and it continues listening for the next menu level

This uses the Twilio REST redirect mechanism rather than in-band audio, so the DTMF reliably reaches the IVR's `<Gather>` element regardless of codec or audio path.

### IVR mock server (`simulator/`)

The `simulator/` module is a standalone FastAPI server that simulates a configurable IVR system. Use it for local testing without needing a real phone system.

#### End-to-end setup

Two servers are needed (agent + IVR), each with its own public URL.

**Step 1 — Start both servers**

```bash
./run.sh all
# agent server → TWILIO_PUBLIC_URL (auto ngrok, changes on restart)
# IVR server   → https://jessi-foxlike-brielle.ngrok-free.dev (static)
```

**Step 2 — Configure Twilio number for IVR**

In [Twilio Console](https://console.twilio.com) → Phone Numbers → Manage → set the IVR number's **Voice URL** to:

```
https://jessi-foxlike-brielle.ngrok-free.dev/twiml   (POST)
```

This URL is static — no need to update it after restarts.

**Step 3 — Place a call**

Open `https://your-ngrok-url/dashboard` and fill in:

- **Phone** — the IVR Twilio number (e.g. `+61257610747`)
- **Goal** — what the agent should find out (e.g. `Find out the pricing for the starter plan`)
- **IVR Mode** — ✅ enable this

Or via the API:

```bash
curl -X POST http://localhost:3040/dashboard/call \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "+61257610747",
    "goal": "Find out the pricing for the starter plan",
    "ivr_mode": true
  }'
```

**Configuration — `simulator/flows/example.yaml`:**

```yaml
name: My IVR
start: welcome

nodes:
  welcome:
    type: say
    say: "Welcome. Please listen carefully."
    next: main_menu

  main_menu:
    type: menu
    say: "Press 1 for sales. Press 2 for support."
    gather:
      timeout: 5
      num_digits: 1
    routes:
      "1": sales
      "2": support
    default: main_menu   # replay menu on invalid input

  sales:
    type: say
    say: "Connecting you to sales."
    next: goodbye

  support:
    type: softphone       # connect browser softphone as operator
    say: "Please hold."

  goodbye:
    type: say
    say: "Thank you. Goodbye."
    next: hangup

  hangup:
    type: hangup
```

**Node types:**

| Type | Description |
|---|---|
| `say` | Speak text, then redirect to `next` node |
| `menu` | Speak prompt inside `<Gather>`, route on DTMF input |
| `softphone` | Speak prompt, then dial browser softphone as operator |
| `pause` | Silence for N seconds, then redirect |
| `hangup` | Hang up the call |

**IVR environment variables:**

| Variable | Description |
|---|---|
| `IVR_BASE_URL` | Public base URL (required — used in TwiML redirects) |
| `IVR_CONFIG` | Path to YAML flow file (default: `flows/example.yaml`) |
| `TWILIO_ACCOUNT_SID` | Same as shuo |
| `TWILIO_AUTH_TOKEN` | Same as shuo |
| `TWILIO_TWIML_APP_SID` | TwiML App SID for browser softphone token |
| `TWILIO_CALLER_ID` | Number to show for outbound browser calls |

**IVR endpoints:**

| Method | Path | Description |
|---|---|---|
| `POST` | `/twiml` | Entry point (Twilio calls this when the number is dialled) |
| `POST` | `/ivr/step?node=ID` | Render a node as TwiML |
| `POST` | `/ivr/gather?node=ID` | Handle DTMF input and route to next node |
| `GET` | `/ivr/token` | Twilio Access Token for IVR browser softphone |
| `GET` | `/phone` | IVR browser softphone UI |
| `GET` | `/health` | Health check |

**Tests:**

```bash
python -m pytest simulator/tests/ -v
```

24 tests covering config parsing, TwiML rendering, routing logic, and full call flow simulation. All run without network access.

---

## Browser softphone

`ui/phone.html` is a static HTML page that registers as a Twilio WebRTC client. Use it to answer calls from the agent (for testing) or to act as an IVR operator.

- Fetches a Twilio Access Token from `/token` (shuo server) or `/ivr/token` (IVR server)
- Requires HTTPS — ngrok provides this automatically
- No server required after page load; communicates directly with Twilio

Open it at `https://your-ngrok-url/phone`.

---

## Agent tool calls

The LLM uses pydantic-ai typed tool calls to control call flow. No text markers are embedded in speech.

| Tool | Effect |
|---|---|
| `press_dtmf(digit)` | Send DTMF digit N via Twilio REST API |
| `go_on_hold()` | Enter hold mode (suppress barge-in) |
| `signal_hangup()` | After playback completes, terminate the call |

Hold detection is automatic: when the agent is placed on hold by the callee, Deepgram continues transcribing the hold music / automated messages. The agent detects this via `go_on_hold()` and stays silent until a real person returns.

---

## Shuo framework internals

The `shuo/` package is the core framework. See [shuo/README.md](shuo/README.md) for a full architecture diagram.

### Key modules

```
shuo/
  call.py             # Events, actions, state (CallState), step(), run_call()
  agent.py            # LLM → TTS → Player pipeline; owns conversation history
  language.py         # LanguageModel (Groq streaming, pydantic-ai tools)
  speech.py           # Transcriber + TranscriberPool (Deepgram Flux)
  voice.py            # VoicePool + AudioPlayer + dtmf_tone()
  voice_elevenlabs.py # ElevenLabs WebSocket streaming TTS
  voice_kokoro.py     # Local Kokoro-82M TTS (zero API cost)
  voice_fish.py       # Fish Audio S2 self-hosted TTS
  phone.py            # TwilioPhone + LocalPhone + dial_out()
  web.py              # FastAPI server — all HTTP/WS endpoints
  log.py              # Colored terminal logging
  tracer.py           # Per-turn latency tracing (saves JSON to /tmp/shuo/)
```

### TTS providers

| Provider | Env var | Notes |
|---|---|---|
| `elevenlabs` | `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | Cloud, best quality |
| `kokoro` | _(none)_ | Local Kokoro-82M via Docker; zero API cost |
| `fish` | _(Fish Audio server URL)_ | Self-hosted Fish Audio S2 |

Set `TTS_PROVIDER` in `.env` to switch providers.

### Latency tracing

Each call writes a JSON trace to `/tmp/shuo/{stream_sid}.json`:

```bash
curl https://your-ngrok-url/trace/latest | python3 -m json.tool
```

The trace contains per-turn spans (LLM first token, TTS first audio, playback complete) with millisecond timestamps relative to turn start.

---

## Server API reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/token` | Twilio Access Token for browser softphone |
| `GET` | `/phone` | Browser softphone UI |
| `GET/POST` | `/twiml` | TwiML webhook — connects Twilio WebSocket |
| `GET/POST` | `/twiml/ivr-dtmf?digit=N` | TwiML to play DTMF then reconnect stream |
| `GET/POST` | `/twiml/conference/{call_id}` | TwiML for supervisor takeover conference leg |
| `WS` | `/ws` | Twilio media stream (one WebSocket per call) |
| `WS` | `/ws-listen` | Listen-only stream for takeover transcription |
| `GET` | `/trace/latest` | Most recent call latency trace (JSON) |
| `GET` | `/call/{number}` | Trigger outbound call via HTTP |

---

## Utility scripts

### `hangup_all.py`

List or terminate all active Twilio calls. Useful when calls get stuck after a server restart.

```bash
python hangup_all.py          # hang up all in-progress calls
python hangup_all.py --list   # list only, don't hang up
```

### `make_call.py`

One-shot script to dial a number using Twilio REST (no FastAPI server needed).

```bash
python make_call.py +61400000000
```

### `restart.sh`

Kills whatever is running on port 3040 and restarts `main.py` with the Kokoro venv. Useful during local development.

```bash
bash restart.sh
```

---

## Tests

```bash
# Core tests (pure, no I/O, ~0.03s)
python -m pytest tests/ -v

# Simulator integration tests (no network, ~1s)
python -m pytest simulator/tests/ -v
```

133/133 tests pass.

---

## Known limitations

- **Mainland China (+86)**: Twilio does not support calls to China.
- **Twilio trial accounts**: Can only call verified numbers.
- **ngrok free tier (agent)**: URL changes on every restart — `TWILIO_PUBLIC_URL` is set automatically by `--ngrok` but you must update any Twilio number webhooks that point to it.
- **IVR ngrok endpoint**: Uses a static cloud endpoint (`https://jessi-foxlike-brielle.ngrok-free.dev`) — URL never changes; connect with `./run.sh ivr` or `ngrok http --url=... 8001`.
- **Browser softphone**: Requires HTTPS (ngrok provides this).

---

## Deployment (Railway)

The `Procfile` is already configured:

```
web: python main.py
```

Set all environment variables in Railway's dashboard. Set `TWILIO_PUBLIC_URL` to your Railway deployment URL. No ngrok needed in production.
