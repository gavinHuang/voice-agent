# voice-agent

A real-time AI voice agent that makes outbound phone calls and lets you monitor and take over live. Built on [shuo](shuo/README.md) — a ~600-line Python voice agent framework.

The agent listens with Deepgram Flux, replies with Groq LLaMA 3.3, and speaks with ElevenLabs — targeting ~400 ms end-to-end latency. When the goal is accomplished it says a short goodbye and hangs up automatically.

---

## Repo layout

```
voice-agent/
  shuo/               # Core voice agent (framework + FastAPI server)
  dashboard/          # Supervisor dashboard (UI, call registry, event bus)
  client/             # Browser softphone for end-to-end testing
  softphone/          # Standalone softphone (static, no server required)
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
| `PORT` | No | Server port (default: `3040`) |
| `DRAIN_TIMEOUT` | No | Seconds to wait for active calls on SIGTERM (default: `300`) |

> **Note:** `CALL_GOAL` and `INITIAL_MESSAGE` are now managed through the dashboard UI — no need to set them in `.env`.

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

## Dashboard workflow

### 1. Place a call

At the top of the dashboard, fill in:

- **Phone** — the number to call in E.164 format (e.g. `+61400000000`), or `client:browser` to call the browser softphone
- **Goal** — what the agent should accomplish (e.g. `Confirm the customer's appointment for tomorrow at 2pm`)

Click **📞 Place Call**. The agent dials out, introduces itself, and works toward the goal.

### 2. Monitor the call

Each active call shows:

- Phone number, goal, and elapsed time
- Live transcript — caller on the left, agent on the right
- Phase badge (`LISTENING` / `RESPONDING`)

### 3. Take over the call

If you want to step in manually:

1. Open `https://your-ngrok-url/phone` in another tab (browser softphone)
2. Click **Register**
3. In the dashboard, click **🎤 Take Over** — the agent goes silent
4. Speak directly to the caller via the softphone
5. Click **🔙 Hand Back** to return control to the agent

### 4. View the outcome

When the call ends the panel stays open with an **OUTCOME** section. Click **✨ Summarize** to generate a one-sentence LLM summary of what was accomplished.

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

## API reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET/POST` | `/twiml` | TwiML webhook for Twilio |
| `WS` | `/ws` | Twilio media stream (one per call) |
| `GET` | `/token` | Twilio Access Token for browser softphone |
| `GET` | `/phone` | Browser softphone UI |
| `GET` | `/call/{number}` | Trigger outbound call via HTTP |
| `GET` | `/dashboard` | Supervisor dashboard UI |
| `WS` | `/dashboard/ws` | Live event stream to dashboard |
| `POST` | `/dashboard/call` | Place call with goal `{phone, goal}` |
| `POST` | `/dashboard/calls/{id}/hangup` | Hang up a call |
| `POST` | `/dashboard/calls/{id}/takeover` | Suppress agent, human takes over |
| `POST` | `/dashboard/calls/{id}/handback` | Return control to agent |
| `POST` | `/dashboard/calls/{id}/dtmf` | Inject DTMF digit |
| `POST` | `/dashboard/summarize` | Generate call outcome summary |

---

## Known limitations

- **Mainland China (+86)**: Twilio does not support calls to China.
- **Twilio trial accounts**: Can only call verified numbers.
- **ngrok free tier**: URL changes on every restart — update `TWILIO_PUBLIC_URL` in `.env` after each restart.
- **Browser softphone**: Requires HTTPS (ngrok provides this).

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
