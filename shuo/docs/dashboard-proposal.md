# Feature Proposal: Real-Time Call Monitoring Dashboard

## Overview

A web-based supervisor dashboard that shows every active call in real time and gives the operator
three intervention tools: **hang up**, **take over via voice**, and **inject DTMF**.

All new code lives outside the `shuo` Python package. Changes to `shuo` are additive-only
(no existing behaviour altered).

---

## Folder Reorganisation

```
shuo/                       ← unchanged Python package
main.py                     ← unchanged entry point
dashboard/                  ← NEW — all dashboard code lives here
  server.py                 # FastAPI app (mounted at /dashboard)
  app.html                  # Single-page dashboard UI (vanilla JS, no framework)
  bus.py                    # In-process event bus: call events → dashboard WS clients
softphone/                  ← MOVE from client/
  phone.html                # Supervisor browser phone (was client/phone.html)
```

The `shuo/server.py` `/phone` endpoint path is updated to point at `softphone/phone.html`.

---

## Architecture

```
                          ┌──────────────────────────────────────┐
                          │           shuo server                │
                          │                                      │
 Twilio WS ───────────►  │  conversation.py                     │
                          │    event loop                        │
                          │    │                                 │
                          │    ├─► (existing) Flux / LLM / TTS  │
                          │    │                                 │
                          │    └─► CallBus.publish(event)  ─────┼──► dashboard/bus.py
                          │                                      │         │
                          │  dashboard/server.py                 │         │
                          │    GET /dashboard/              ─────┼── app.html
                          │    WS  /dashboard/ws/{call_id} ◄────┼── bus subscribers
                          │    POST /dashboard/calls/{id}/hangup │
                          │    POST /dashboard/calls/{id}/takeover│
                          │    POST /dashboard/calls/{id}/handback│
                          │    POST /dashboard/calls/{id}/dtmf   │
                          └──────────────────────────────────────┘
                                         │
                          ┌──────────────┴──────────────┐
                          │    Supervisor Browser        │
                          │  dashboard/app.html          │
                          │  ┌──────────────────────┐   │
                          │  │ transcript (stream)  │   │
                          │  │ agent tokens (stream)│   │
                          │  │ phase indicator      │   │
                          │  │ goal / call info     │   │
                          │  ├──────────────────────┤   │
                          │  │ [Hang Up]            │   │
                          │  │ [Take Over Voice]    │   │
                          │  │ [DTMF Keypad]        │   │
                          │  │ [Hand Back]          │   │
                          │  └──────────────────────┘   │
                          └──────────────────────────────┘
```

---

## Dashboard UI Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  shuo monitor                                  active calls: 2   │
├──────────────────────────────────────────────────────────────────┤
│  ▼ Call: +1 (415) 555-0192   [RESPONDING]   ⏱ 02:34             │
│    Goal: Book dental cleaning appointment                        │
├────────────────────────┬─────────────────────────────────────────┤
│ USER TRANSCRIPT        │ AGENT RESPONSE                          │
│                        │                                         │
│  "I'd like to make     │  "Of course! I can help schedule        │
│   an appointment for   │   that. What date works best           │
│   a cleaning..."       │   for you?..."  ▌ (streaming)          │
│                        │                                         │
├────────────────────────┴─────────────────────────────────────────┤
│  [🔴 Hang Up]  [🎤 Take Over]  [🔙 Hand Back]  [📱 Keypad ▾]    │
│                                                                  │
│  DTMF Keypad (collapsed by default):                             │
│    [1][2][3]   [4][5][6]   [7][8][9]   [*][0][#]               │
└──────────────────────────────────────────────────────────────────┘
```

Multiple calls stack vertically. Each is independently controllable.

---

## Event Bus (`dashboard/bus.py`)

A thin in-process pub/sub layer. The shuo conversation loop publishes; dashboard WebSocket connections subscribe.

```python
# dashboard/bus.py

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Callable, Awaitable

@dataclass
class CallBus:
    """One bus per active call. Shared between conversation loop and dashboard WS clients."""
    call_id: str
    subscribers: List[asyncio.Queue] = field(default_factory=list)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.remove(q)

    def publish(self, event: dict) -> None:
        for q in self.subscribers:
            q.put_nowait(event)


# Global registry: call_id → CallBus
_registry: Dict[str, CallBus] = {}

def create(call_id: str) -> CallBus:
    bus = CallBus(call_id=call_id)
    _registry[call_id] = bus
    return bus

def get(call_id: str) -> CallBus | None:
    return _registry.get(call_id)

def destroy(call_id: str) -> None:
    _registry.pop(call_id, None)

def all_buses() -> List[CallBus]:
    return list(_registry.values())
```

---

## Minimal Changes to `shuo` Package

Only two files need additive changes. No existing logic is altered.

### `shuo/conversation.py` — add `observer` hook

Add an optional `observer: Callable[[dict], None]` parameter to `run_conversation_over_twilio`.
After each event is processed (and after LLM tokens via agent), call `observer(event_dict)`.

```python
# Signature change only — no logic change
async def run_conversation_over_twilio(
    websocket: WebSocket,
    observer: Callable[[dict], None] | None = None,   # NEW
) -> None:
    ...
    # After state, actions = process_event(state, event):
    if observer:
        observer({"type": type(event).__name__, "phase": state.phase.name, ...})
```

The `observer` is called for:
- `stream_start` — `{type, call_id, phone_number}`
- `stream_stop` — `{type}`
- `transcript` — `{type, text}` (from FluxEndOfTurnEvent)
- `phase_change` — `{type, from, to}` (LISTENING ↔ RESPONDING)
- `agent_token` — `{type, token}` (from Agent's `_on_llm_token`)
- `agent_done` — `{type}`
- `dtmf` — `{type, digit}` (from MarkerScanner)
- `hold` / `hold_end` — `{type}`

### `shuo/agent.py` — expose token stream

Add optional `on_token_observed: Callable[[str], None] | None = None` to `Agent.__init__`.
Call it inside `_on_llm_token` after existing logic.

```python
# In _on_llm_token, after existing processing:
if self._on_token_observed and clean_text:
    self._on_token_observed(clean_text)
```

### `shuo/server.py` — wire dashboard

```python
# In websocket_endpoint, create a bus and pass observer:
from dashboard.bus import create as create_bus, destroy as destroy_bus

bus = create_bus(call_id=stream_sid_placeholder)
try:
    await run_conversation_over_twilio(websocket, observer=bus.publish)
finally:
    destroy_bus(...)
```

---

## Dashboard Server (`dashboard/server.py`)

Mounted as a sub-application or run on the same FastAPI instance.

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/dashboard/` | Serve `app.html` |
| `WS` | `/dashboard/ws/{call_id}` | Stream events to browser |
| `GET` | `/dashboard/calls` | List active calls (for initial page load) |
| `POST` | `/dashboard/calls/{call_id}/hangup` | Terminate call |
| `POST` | `/dashboard/calls/{call_id}/takeover` | Human takes over |
| `POST` | `/dashboard/calls/{call_id}/handback` | Return control to agent |
| `POST` | `/dashboard/calls/{call_id}/dtmf` | Inject a DTMF digit |

### WebSocket event stream

All events are JSON lines on the dashboard WebSocket:

```json
{"type": "stream_start",  "call_id": "MZ...", "phone": "+14155550192", "goal": "Book appointment"}
{"type": "transcript",    "text": "I'd like to book a cleaning"}
{"type": "phase_change",  "from": "LISTENING", "to": "RESPONDING"}
{"type": "agent_token",   "token": "Of course"}
{"type": "agent_token",   "token": "! I can"}
{"type": "agent_done"}
{"type": "dtmf",          "digit": "1"}
{"type": "hold"}
{"type": "hold_end"}
{"type": "takeover",      "mode": "voice"}
{"type": "handback"}
{"type": "stream_stop"}
```

---

## Feature: Hang Up

**Flow:**
1. Supervisor clicks `[Hang Up]` in dashboard.
2. Browser `POST /dashboard/calls/{call_id}/hangup`.
3. Server calls Twilio REST API: `client.calls(call_sid).update(status="completed")`.
4. Twilio sends `StreamStopEvent` to the existing WebSocket; shuo cleans up normally.

No shuo code changes needed — Twilio drives the teardown through the existing path.

---

## Feature: Take Over via Voice

**Mechanism:** Twilio conference rooms with coach mode.

**Required TwiML change:** Replace the current `<Stream>` with a `<Conference>` so that
participants can join/leave dynamically.

```
Current TwiML:
  <Connect><Stream url="wss://..."/></Connect>

New TwiML (when conference mode enabled):
  <Dial><Conference waitUrl="" beep="false" startConferenceOnEnter="true">
    call-{call_id}
  </Conference></Dial>
```

The shuo agent participates as a "silent moderator" leg (programmatically added by the server on call start via Twilio Participants API).

**Takeover flow:**
1. Supervisor clicks `[Take Over]` → browser requests dashboard WebSocket for a Twilio token.
2. Server generates Twilio Access Token with `VoiceGrant` for the supervisor browser identity.
3. Server calls Twilio REST to add the supervisor as a participant in conference `call-{call_id}`:
   - `muted=false`, `coach=<agent_participant_sid>` (agent can hear supervisor but caller cannot).
   - Or full barge-in: `coach=None` (three-way call — supervisor, agent, caller all hear each other).
4. Simultaneously, server sets an in-memory flag `call.mode = TAKEOVER`.
5. The shuo agent's `_on_llm_token` checks `call.mode` before sending to TTS — if `TAKEOVER`, tokens are still observed (shown in dashboard) but not sent to TTS. Effectively agent goes silent.
6. Dashboard shows `[TAKEN OVER — speaking as supervisor]` indicator.

**Handback flow:**
1. Supervisor clicks `[Hand Back]`.
2. Server removes supervisor from the conference participant list (Twilio REST).
3. Server clears `call.mode` flag → agent resumes TTS on next turn.

---

## Feature: DTMF Keypad

Two injection paths depending on where in the pipeline we are:

### Path A — Inject via AudioPlayer (in-band, low latency)

The dashboard server holds a reference to the live `Agent` for the call. When a digit arrives:

```python
# dashboard/server.py
@app.post("/dashboard/calls/{call_id}/dtmf")
async def inject_dtmf(call_id: str, body: DTMFRequest):
    agent = call_registry.get_agent(call_id)
    if agent and agent._player:
        from shuo.services.dtmf import generate_dtmf_ulaw_b64
        audio = generate_dtmf_ulaw_b64(body.digit)
        await agent._player.send_chunk(audio)
```

Reuses the existing `generate_dtmf_ulaw_b64()` — no new code needed.

### Path B — Inject via Twilio REST API (works even if agent is mid-response)

```python
client.calls(call_sid).update(
    twiml=f'<Response><Play digits="{digit}"/></Response>'
)
```

Path A is preferred when the agent is silent (LISTENING phase). Path B is needed when
the agent is speaking and the DTMF must be sent immediately regardless of the audio buffer.

### Keypad UI

Digit buttons in the dashboard send `POST /dashboard/calls/{call_id}/dtmf` with `{"digit": "5"}`.
The supervisor can also type digits on their keyboard while focused on a call panel.

---

## Call Goal

The `goal` field shown in the dashboard comes from the call config:

- If using the planned configurable call API (see `docs/api-plan.md`), `goal` is a field in the
  `CallConfig` (e.g. `"goal": "Schedule dental appointment"`).
- For now, it can be inferred from the system prompt first sentence, or set as an env var
  `CALL_GOAL` and passed through.
- The dashboard shows it as a read-only banner above the transcript panes.

---

## New Files

```
dashboard/
  __init__.py
  bus.py          # In-process event bus (CallBus)
  server.py       # FastAPI sub-app with all /dashboard/* endpoints
  registry.py     # Active call registry: call_id → {call_sid, agent ref, mode, goal}
  app.html        # Self-contained dashboard SPA (vanilla JS + SSE/WS)

softphone/
  phone.html      # Moved from client/phone.html (supervisor browser phone)
```

### `dashboard/registry.py` — call registry

```python
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, Optional

class CallMode(Enum):
    AGENT    = auto()   # Normal — agent is in control
    TAKEOVER = auto()   # Human supervisor is speaking

@dataclass
class ActiveCall:
    call_id:   str
    call_sid:  str
    phone:     str
    goal:      str
    mode:      CallMode = CallMode.AGENT
    agent:     object = None   # shuo Agent ref (for DTMF inject)
    started_at: float = 0.0

_calls: Dict[str, ActiveCall] = {}

def register(call: ActiveCall) -> None:   _calls[call.call_id] = call
def get(call_id: str) -> Optional[ActiveCall]: return _calls.get(call_id)
def remove(call_id: str) -> None:         _calls.pop(call_id, None)
def all_calls() -> list:                  return list(_calls.values())
```

---

## Environment Variables (new)

| Variable | Required | Default | Description |
|---|---|---|---|
| `DASHBOARD_AUTH_TOKEN` | No | — | Bearer token to protect dashboard endpoints (recommended for production) |
| `CALL_GOAL` | No | `""` | Default goal shown in dashboard when no per-call goal is set |

---

## Implementation Steps

1. **Create `dashboard/bus.py`** — event bus (no shuo changes).
2. **Create `dashboard/registry.py`** — call registry.
3. **Modify `shuo/conversation.py`** — add `observer` param (6 lines added).
4. **Modify `shuo/agent.py`** — add `on_token_observed` param (3 lines added).
5. **Modify `shuo/server.py`** — wire bus + registry on WS connect/disconnect.
6. **Create `dashboard/server.py`** — all dashboard endpoints.
7. **Create `dashboard/app.html`** — single-file dashboard UI.
8. **Move `client/phone.html` → `softphone/phone.html`** — update server path.
9. **Update `shuo/server.py` `/phone` endpoint** — new path.
10. **Update `requirements.txt`** — no new dependencies (uses existing FastAPI, twilio, asyncio).

---

## Minimal `shuo` Change Summary

| File | Change | Lines added |
|---|---|---|
| `shuo/conversation.py` | Add `observer` callback param; call it per event | ~10 |
| `shuo/agent.py` | Add `on_token_observed` param; call it in `_on_llm_token` | ~5 |
| `shuo/server.py` | Wire bus + registry; update `/phone` path | ~20 |

Zero changes to `state.py`, `types.py`, `services/*`, or `tracer.py`.

Total new code: ~500 lines across 4 new files + 1 HTML file. shuo itself grows by ~35 lines.
