# voice-agent

A real-time AI voice agent that makes and receives phone calls. Built on [shuo](shuo/README.md) — a ~600-line Python voice agent framework.

The agent listens, understands speech with Deepgram Flux, generates replies with Groq LLaMA 3.3, and speaks back with ElevenLabs — targeting ~400 ms end-to-end latency.

---

## What's in this repo

```
voice-agent/
  shuo/               # Core voice agent (framework + server + entry point)
  dashboard/          # Web dashboard (call registry, live monitoring)
    server.py         # Dashboard FastAPI server
    app.html          # Dashboard UI
    registry.py       # Call registry
    bus.py            # Event bus
  client/             # Browser softphone for end-to-end testing
    phone.html        # Softphone UI (served via ngrok HTTPS)
  softphone/          # Standalone softphone (static, no server required)
    phone.html        # Self-contained softphone page
  README.md           # This file
```

---

## Prerequisites

- Python 3.9+
- [ngrok](https://ngrok.com/) (for local development)
- API keys for: **Twilio**, **Deepgram**, **Groq**, **ElevenLabs**

---

## Setup

```bash
cd shuo
pip install -r requirements.txt
cp .env.example .env   # then fill in your keys (see Configuration below)
```

Start ngrok in a separate terminal:

```bash
ngrok http 3040
```

Copy the `https://` URL ngrok gives you and set it as `TWILIO_PUBLIC_URL` in your `.env`.

---

## Configuration

All config lives in `shuo/.env`. Copy from `.env.example` and fill in:

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
| `INITIAL_MESSAGE` | No | If set, agent speaks this first when a call connects (outbound greeting) |
| `PORT` | No | Server port (default: `3040`) |
| `DRAIN_TIMEOUT` | No | Seconds to wait for active calls on SIGTERM (default: `300`) |

To create a Twilio API Key (needed for browser softphone):
Twilio Console → Account → API Keys → Create new key → copy SID and Secret.

---

## Running

All commands are run from the `shuo/` directory.

### Inbound mode — wait for calls

```bash
cd shuo
python main.py
```

Configure your Twilio number's Voice webhook to: `https://your-ngrok-url/twiml`

### Outbound call to a real phone number

```bash
cd shuo
python main.py +61400000000
```

Or trigger via HTTP while the server is already running:

```bash
curl https://your-ngrok-url/call/+61400000000
```

Set `INITIAL_MESSAGE` in `.env` to make the agent speak first when the call connects:

```
INITIAL_MESSAGE=Hello! I'm calling to let you know your parcel has arrived. Can you collect it today?
```

### Browser softphone (for testing without a physical phone)

1. Start the server: `python main.py`
2. Open `https://your-ngrok-url/phone` in a browser
3. Click **Register** — the browser becomes a softphone client
4. Trigger a call to `client:browser`: `python main.py client:browser`
5. Click **Answer** when the call comes in — speak naturally, the agent responds

The browser softphone uses the Twilio Voice SDK. It requires `TWILIO_API_KEY` and `TWILIO_API_SECRET` to be set.

---

## Quick start: make a call and monitor it

### 1. Start the server

```bash
cd shuo
python main.py
```

### 2. Place an outbound call with a task

Set `CALL_GOAL` so the agent knows what it's trying to accomplish, then trigger the call:

```bash
# Set the goal for this call
export CALL_GOAL="Confirm the customer's appointment for tomorrow at 2pm"

# Trigger a call to a phone number
python main.py +61400000000
```

Or trigger a call via HTTP while the server is already running:

```bash
CALL_GOAL="Remind the customer their subscription renews tomorrow" \
  python main.py  # server already running in another terminal

curl https://your-ngrok-url/call/+61400000000
```

`CALL_GOAL` is available to the agent as context for the conversation. If left unset, the agent uses the default system prompt.

### 3. View the dashboard

Open the dashboard in your browser to monitor live calls in real time:

```
https://your-ngrok-url/dashboard
```

The dashboard shows:
- All active calls with phone number, goal, and elapsed time
- Live transcript as the conversation unfolds
- Controls to **hang up**, **take over** (mute the agent and speak yourself via the softphone), or **hand back** to the agent

To take over a call manually:
1. Open `https://your-ngrok-url/phone` in a second tab (the browser softphone)
2. Click **Register**
3. In the dashboard, click **Take over** on the active call — the agent goes silent
4. Speak directly to the caller via the softphone
5. Click **Hand back** to return control to the agent

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

Everything streams end-to-end — LLM tokens feed TTS immediately, TTS audio feeds Twilio immediately. Barge-in (interruption) cancels the agent pipeline instantly.

### State machine

```
LISTENING ──EndOfTurn──► RESPONDING ──Done──► LISTENING
    ▲                        │
    └────StartOfTurn─────────┘  (barge-in cancels agent)
```

---

## API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check → `{"status": "ok"}` |
| `GET/POST` | `/twiml` | TwiML webhook — Twilio calls this to get WebSocket URL |
| `WS` | `/ws` | Twilio media stream — one connection per active call |
| `GET` | `/token` | Issues a Twilio Access Token for the browser softphone |
| `GET` | `/phone` | Browser softphone UI |
| `GET` | `/call/{number}` | Trigger an outbound call, e.g. `/call/+61400000000` |
| `GET` | `/trace/latest` | Most recent call latency trace as JSON |
| `GET` | `/bench/ttft` | TTFT benchmark across OpenAI/Groq models |

---

## Known limitations

- **Mainland China (+86)**: Twilio does not support calls to China. Use Vonage or Alibaba Cloud Voice as alternatives.
- **Twilio trial accounts**: Can only call verified numbers. Upgrade to a paid account to call any number.
- **ngrok free tier**: URL changes every restart — update `TWILIO_PUBLIC_URL` in `.env` after each restart.
- **Browser softphone**: Requires HTTPS (ngrok provides this). Won't work on plain `http://localhost`.

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
cd shuo
python -m pytest tests/ -v   # pure unit tests, no I/O, runs in ~0.03s
```

See [`shuo/docs/project-description.md`](shuo/docs/project-description.md) for a detailed technical reference covering the architecture, state machine, latency optimizations, and planned extensions.
