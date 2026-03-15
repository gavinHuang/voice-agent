# voice-agent

A real-time AI voice agent that makes outbound phone calls and lets you monitor and take over live. Built on [shuo](shuo/README.md) — a ~600-line Python voice agent framework.

The agent listens with Deepgram Flux, replies with Groq LLaMA 3.3, and speaks with ElevenLabs (or a local Kokoro model) — targeting ~400 ms end-to-end latency. When the goal is accomplished it says a short goodbye and hangs up automatically.

---

## Repo layout

```
voice-agent/
  shuo/               # Core voice agent framework + FastAPI server
  dashboard/          # Supervisor dashboard (UI, call registry, event bus)
  ivr/                # IVR mock server (config-driven, YAML call flows)
  softphone/          # Browser softphone (static, no server required)
  client/             # Alternate browser softphone for testing
  make_call.py        # One-shot outbound call script
  hangup_all.py       # Utility to terminate all active Twilio calls
  restart.sh          # Dev restart script (kills port 3040, restarts server)
```

---

## Prerequisites

- Python 3.9+
- [ngrok](https://ngrok.com/) (for local development — Twilio needs a public HTTPS URL)
- API keys for: **Twilio**, **Deepgram**, **Groq**, **ElevenLabs**

---

## Setup

```bash
cd shuo
pip install -r requirements.txt
cp .env.example .env   # fill in your keys (see Configuration below)
```

Start ngrok in a separate terminal:

```bash
ngrok http 3040
```

Copy the `https://` URL ngrok gives you and set it as `TWILIO_PUBLIC_URL` in `shuo/.env`.

---

## Configuration

All config lives in `shuo/.env`:

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
cd shuo
python main.py
```

Then open the dashboard:

```
https://your-ngrok-url/dashboard
```

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

Everything streams end-to-end. LLM tokens feed TTS immediately; TTS audio feeds Twilio immediately. Barge-in cancels the agent pipeline instantly. When the agent emits `[HANGUP]` after completing its goal, the call is terminated via the Twilio REST API.

See [shuo/README.md](shuo/README.md) for the framework internals — state machine, architecture diagram, and project structure.

---

## Dashboard

The supervisor dashboard lives at `GET /dashboard` and is served by the `dashboard/` module.

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
2. The LLM hears the menu prompt and replies with a marker only, e.g. `[DTMF:2]`
3. The server redirects the call to `/twiml/ivr-dtmf?digit=2`, which sends the real DTMF tone to the remote party via `<Play digits="2"/>`
4. The stream reconnects, the agent's conversation history is preserved, and it continues listening for the next menu level

This uses the Twilio REST redirect mechanism rather than in-band audio, so the DTMF reliably reaches the IVR's `<Gather>` element regardless of codec or audio path.

### IVR mock server (`ivr/`)

The `ivr/` module is a standalone FastAPI server that simulates a configurable IVR system. Use it for local testing without needing a real phone system.

**Start:**

```bash
IVR_BASE_URL=https://your-tunnel-url python3 -m uvicorn ivr.server:app --port 8001
```

The IVR server needs its own public URL (separate from the shuo server). Use a second tunnel:

```bash
# second ngrok session (paid), or:
ssh -R 80:localhost:8001 nokey@localhost.run
```

Set that URL as the voice URL on a second Twilio number, then call that number with IVR Mode enabled.

**Configuration — `ivr/flows/example.yaml`:**

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
python -m pytest ivr/tests/ -v
```

24 tests covering config parsing, TwiML rendering, routing logic, and full call flow simulation. All run without network access.

---

## Browser softphone

`softphone/phone.html` is a static HTML page that registers as a Twilio WebRTC client. Use it to answer calls from the agent (for testing) or to act as an IVR operator.

- Fetches a Twilio Access Token from `/token` (shuo server) or `/ivr/token` (IVR server)
- Requires HTTPS — ngrok provides this automatically
- No server required after page load; communicates directly with Twilio

Open it at `https://your-ngrok-url/phone`.

---

## Agent markers

The LLM can embed control markers in its response. The `MarkerScanner` strips them before TTS so they are never spoken aloud.

| Marker | Trigger | Effect |
|---|---|---|
| `[DTMF:N]` | LLM output | Send DTMF digit N via Twilio REST API |
| `[HOLD]` | LLM output | Enter hold mode (suppress barge-in) |
| `[HOLD_CONTINUE]` | LLM output | Still on hold — skip TTS, end turn silently |
| `[HOLD_END]` | LLM output | Exit hold mode, resume normal conversation |
| `[HANGUP]` | LLM output | After playback completes, terminate the call |

Hold detection is automatic: when the agent is placed on hold by the callee, Deepgram continues transcribing the hold music / automated messages. The agent receives `[HOLD_CHECK]` prompts and responds with `[HOLD_CONTINUE]` until a real person returns.

---

## Shuo framework internals

The `shuo/` module is the core framework. See [shuo/README.md](shuo/README.md) for a full architecture diagram.

### Key files

```
shuo/shuo/
  types.py              # Immutable state, events, actions
  state.py              # Pure state machine — process_event() ~30 lines
  conversation.py       # Main event loop (receive → update → dispatch)
  agent.py              # LLM → TTS → Player pipeline; owns history
  log.py                # Colored terminal logging
  tracer.py             # Per-turn latency tracing (saves JSON to /tmp/shuo/)
  server.py             # FastAPI server — all HTTP/WS endpoints

  services/
    flux.py             # Deepgram Flux (always-on STT + turn detection)
    flux_pool.py        # Pre-warmed Deepgram connection pool
    llm.py              # Groq LLaMA streaming with conversation history
    tts.py              # TTS provider factory (elevenlabs / kokoro / fish)
    tts_elevenlabs.py   # ElevenLabs WebSocket streaming TTS
    tts_kokoro.py       # Local Kokoro-82M TTS (zero API cost)
    tts_fish.py         # Fish Audio S2 self-hosted TTS
    tts_pool.py         # Pre-warmed TTS connection pool
    player.py           # Async audio player → Twilio WebSocket
    dtmf.py             # DTMF tone generator (μ-law 8kHz)
    twilio_client.py    # Outbound call + WebSocket message parsing
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

Kills whatever is running on port 3040 and restarts `shuo/main.py` with the Kokoro venv. Useful during local development.

```bash
bash restart.sh
```

---

## Known limitations

- **Mainland China (+86)**: Twilio does not support calls to China.
- **Twilio trial accounts**: Can only call verified numbers.
- **ngrok free tier**: URL changes on every restart — update `TWILIO_PUBLIC_URL` in `.env` and re-configure any Twilio number webhooks.
- **Browser softphone**: Requires HTTPS (ngrok provides this).
- **Two tunnels for IVR testing**: The IVR server needs its own public URL. Use `localhost.run` (`ssh -R 80:localhost:8001 nokey@localhost.run`) for a free second tunnel.

---

## Deployment (Railway)

The `shuo/Procfile` is already configured:

```
web: uvicorn shuo.server:app --host 0.0.0.0 --port $PORT
```

Set all environment variables in Railway's dashboard. Set `TWILIO_PUBLIC_URL` to your Railway deployment URL. No ngrok needed in production.

---

## Development

```bash
# Unit tests (shuo core — pure, no I/O, ~0.03s)
cd shuo && python -m pytest tests/ -v

# IVR integration tests (no network, ~1s)
python -m pytest ivr/tests/ -v
```
