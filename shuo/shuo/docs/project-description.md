# Shuo Voice Agent — Project Description

A low-latency outbound voice agent built on Twilio, Deepgram Flux, Groq LLM, and ElevenLabs TTS.

---

## 1. Architecture Overview

Turn-taking orchestration loop with pipelined streaming:

```
Twilio → WebSocket audio (8 kHz μ-law)
  → Deepgram Flux (STT + VAD turn detection)
  → Groq LLM (llama-3.3-70b)
  → ElevenLabs TTS (ulaw_8000)
  → Twilio
```

---

## 2. Core Design Principles

- **Pure state machine**: `process_event(state, event) → (state, actions)` — no side effects
- **Pipelined streaming**: LLM tokens → TTS → Twilio without buffering
- **TTS connection pool**: pre-warms ElevenLabs WebSocket connections (~200–300 ms saving)
- **Barge-in**: `FluxStartOfTurnEvent` cancels LLM + TTS + Player simultaneously
- **Tracer**: records latency spans (tts_pool, llm, tts, player) per turn

---

## 3. File Structure

```
main.py                          Entry point (server + optional outbound call)
requirements.txt
shuo/
  types.py                       Immutable events / actions / state (dataclasses)
  state.py                       Pure state machine: (State, Event) → (State, Actions)
  conversation.py                Main async event loop
  agent.py                       LLM→TTS→Player pipeline, owns conversation history
  log.py                         Coloured logging (ServiceLogger, Logger)
  server.py                      FastAPI: /health /twiml /ws /trace/latest /bench/ttft
  tracer.py                      Latency span tracer, saves to /tmp/shuo/<call_id>.json
  docs/
    project-description.md       This file
  services/
    flux.py                      Deepgram Flux v2 STT + turn detection
    llm.py                       Groq/OpenAI streaming LLM
    tts.py                       ElevenLabs WebSocket TTS
    tts_pool.py                  Pre-connected TTS connection pool (TTL eviction)
    player.py                    Audio playback loop to Twilio (~20 ms chunks)
    dtmf.py                      DTMF tone generator (μ-law 8 kHz)
    twilio_client.py             Outbound calls + Twilio message parsing
```

---

## 4. State Machine

`AppState` fields:

| Field | Type | Meaning |
|---|---|---|
| `phase` | `Phase` | `LISTENING` or `RESPONDING` |
| `stream_sid` | `str \| None` | Twilio stream identifier |
| `hold_mode` | `bool` | True while agent is waiting on hold |

Events flow through `process_event(state, event)` which returns `(new_state, actions)`.

---

## 5. Event Types

| Event | Source | Meaning |
|---|---|---|
| `StreamStartEvent` | Twilio | WebSocket stream opened |
| `StreamStopEvent` | Twilio | Call ended |
| `MediaEvent` | Twilio | Raw audio packet |
| `FluxStartOfTurnEvent` | Deepgram | User started speaking (barge-in trigger) |
| `FluxEndOfTurnEvent` | Deepgram | User finished speaking (transcript ready) |
| `AgentTurnDoneEvent` | Agent | Playback complete |
| `HoldStartEvent` | Agent | LLM detected hold music; entering hold mode |
| `HoldEndEvent` | Agent | LLM detected real person; exiting hold mode |

---

## 6. Action Types

| Action | Effect |
|---|---|
| `FeedFluxAction` | Send audio bytes to Deepgram |
| `StartAgentTurnAction` | Start LLM→TTS→Player pipeline |
| `ResetAgentTurnAction` | Cancel pipeline + clear Twilio buffer |

`StartAgentTurnAction` carries a `hold_check: bool` flag that instructs the agent to
prepend hold-check context to the LLM message.

---

## 7. Agent Pipeline

```
start_turn(transcript, hold_check)
  ├── acquire TTS connection from pool
  ├── create AudioPlayer
  └── start LLM streaming

LLM token stream
  └── MarkerScanner (strips [DTMF:N] / [HOLD*] markers)
        ├── clean text → TTSService.send()
        ├── DTMF digits → queued for post-TTS playback
        ├── [HOLD]     → emit HoldStartEvent
        ├── [HOLD_END] → emit HoldEndEvent
        └── [HOLD_CONTINUE] → absorbed silently

LLM done
  ├── flush TTS (if any text was sent)
  └── if no text (HOLD_CONTINUE) → cancel TTS, emit AgentTurnDoneEvent immediately

TTS done
  ├── append DTMF tone chunks to player
  └── mark player EOF

Player done
  └── emit AgentTurnDoneEvent
```

---

## 8. Latency Targets

- **End-to-end TTFT**: ~400 ms (EU deployment, Groq LLM)
- Traced spans: `tts_pool`, `llm`, `tts`, `player`
- Trace saved to `/tmp/shuo/<call_id>.json` after each call

---

## 9. Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `TWILIO_ACCOUNT_SID` | — | Twilio credentials |
| `TWILIO_AUTH_TOKEN` | — | Twilio credentials |
| `TWILIO_PHONE_NUMBER` | — | Outbound caller ID |
| `TWILIO_PUBLIC_URL` | — | Webhook base URL |
| `DEEPGRAM_API_KEY` | — | Deepgram Flux STT |
| `GROQ_API_KEY` | — | Groq LLM |
| `LLM_MODEL` | `llama-3.3-70b-versatile` | Groq model |
| `OPENAI_API_KEY` | — | Fallback / benchmark |
| `ELEVENLABS_API_KEY` | — | ElevenLabs TTS |
| `ELEVENLABS_VOICE_ID` | `21m00Tcm4TlvDq8ikWAM` | TTS voice |
| `PORT` | `3040` | Server port |
| `DRAIN_TIMEOUT` | `300` | Graceful shutdown timeout |

---

## 10. Usage

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in API keys
python main.py                  # inbound calls only
python main.py +1234567890      # outbound call
```

---

## 11. Services Detail

### Deepgram Flux (`flux.py`)
- Uses Flux v2 WebSocket API (EU endpoint: `wss://api.eu.deepgram.com`)
- Combines VAD + STT in one stream
- Fires `on_start_of_turn` (barge-in) and `on_end_of_turn` (transcript ready)

### LLM (`llm.py`)
- OpenAI-compatible streaming via Groq
- Persistent conversation history across turns
- System prompt includes IVR navigation and hold-mode instructions

### TTS Pool (`tts_pool.py`)
- Maintains a pool of pre-connected ElevenLabs WebSocket connections
- TTL eviction prevents stale connections
- `get()` returns a warm connection instantly or opens a new one

### Audio Player (`player.py`)
- Independent playback loop: drips chunks at ~20 ms intervals
- Can be topped up dynamically (streaming TTS)
- `stop_and_clear()` sends Twilio `clear` message for instant interrupt

---

## 12. Tracer

Records named spans per turn:

```json
{
  "turn": 1,
  "transcript": "Hello",
  "spans": {
    "tts_pool": {"start": 0, "end": 12},
    "llm": {"start": 12, "end": 245},
    "tts": {"start": 12, "end": 380},
    "player": {"start": 380, "end": 2100}
  }
}
```

Saved to `/tmp/shuo/<stream_sid>.json`. Accessible via `GET /trace/latest`.

---

## 13. Deployment Notes

- Designed for Railway EU (Frankfurt Twilio edge)
- `/tmp/shuo/` for trace files (Linux path)
- ElevenLabs model: `eleven_turbo_v2_5`, output: `ulaw_8000`
- Twilio `edge="frankfurt"` for EU routing

---

## 14. IVR Navigation and Hold Mode

### DTMF IVR Navigation

When the agent needs to navigate an automated phone menu (IVR), it embeds `[DTMF:N]`
markers in its LLM response text. The `MarkerScanner` in `agent.py` intercepts these
markers before they reach TTS, queues the digit, and appends the corresponding tone
audio to the player after TTS speech completes.

**Example LLM response:**
```
I'll select Sales for you. [DTMF:1]
```

The text `"I'll select Sales for you."` is spoken normally, then a 200 ms DTMF
tone for digit `1` (697 Hz + 1209 Hz) is played.

**Supported digits:** `0–9`, `*`, `#`

**DTMF tone generation** (`services/dtmf.py`):
- Generates a sum of two sinusoids at the standard DTMF frequencies
- 8000 Hz sample rate, 200 ms duration (configurable)
- Encoded as μ-law PCM, base64-encoded — same format as ElevenLabs TTS output

### Hold Mode

When the agent is put on hold, the LLM uses `[HOLD]` to signal that hold mode should
begin. The state machine sets `hold_mode=True`, which suppresses barge-in interrupts
so hold music does not trigger a reset.

On each subsequent Flux turn event (hold music transcript), `hold_check=True` is
passed to `agent.start_turn()`, which prepends a `[HOLD_CHECK]` context message to
the LLM prompt. The LLM replies with:

- `[HOLD_CONTINUE]` — still on hold; agent emits `AgentTurnDoneEvent` silently
  (no TTS, no audio)
- `[HOLD_END] <response>` — real person detected; agent emits `HoldEndEvent`,
  then speaks the response normally

**Marker summary:**

| Marker | LLM emits when... | Agent action |
|---|---|---|
| `[DTMF:N]` | IVR menu needs digit N pressed | Queue digit, play tone after TTS |
| `[HOLD]` | Entering hold (e.g. "Please hold") | Emit `HoldStartEvent` → `hold_mode=True` |
| `[HOLD_CONTINUE]` | Still hearing hold music | Silent turn end |
| `[HOLD_END]` | Real person speaking again | Emit `HoldEndEvent` → `hold_mode=False` |
