# Phase 1: ISP Abstraction - Research

**Researched:** 2026-03-21
**Domain:** Python async service abstraction / refactoring Twilio coupling
**Confidence:** HIGH (all findings based on direct code inspection)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- ISP protocol: `typing.Protocol` (structural typing, no ABCs)
- Full 6-method interface: `start(on_media, on_start, on_stop)`, `stop()`, `send_audio(payload)`, `send_dtmf(digit)`, `hangup()`, `call(phone, twiml_url)`
- Inbound audio: ISP owns background task, pushes via callbacks (same pattern as FluxService)
- Outbound audio: AudioPlayer calls `await isp.send_audio(payload)` per μ-law chunk
- DTMF: `await isp.send_dtmf(digit)` — TwilioISP uses REST redirect, LocalISP delivers DTMFToneEvent to peer queue
- Call/hangup: on ISP — TwilioISP wraps existing REST, LocalISP uses no-op/signal

### Claude's Discretion
- How `LocalISP` instances are paired (pair factory, shared session object, or explicit connect)
- Whether `VoiceSession` becomes a class or stays a function with ISP injected as a parameter
- Where `TwilioISP` lives (`shuo/shuo/services/twilio_isp.py` is a reasonable location)
- How much of `server.py`'s WebSocket route changes — TwilioISP should absorb the Twilio-specific parsing
- `AudioPlayer` internal changes (it currently holds a WebSocket reference; it'll need an ISP reference instead)

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| ISP-01 | System defines an `ISP` protocol interface with `send_audio`, `recv_audio`, `send_dtmf`, `hangup`, `call` methods | Protocol definition location and exact shape documented below |
| ISP-02 | Existing Twilio integration is refactored to implement the `ISP` protocol without changing external behavior | All Twilio coupling points identified; move plan documented |
| ISP-03 | A `LocalISP` implementation routes audio between two in-process agents via asyncio queues | Pairing design recommended; async queue approach documented |
| ISP-04 | `VoiceSession` accepts any `ISP` implementation (dependency injection, not hard-coded Twilio) | VoiceSession shape and constructor signature recommended |
| ISP-05 | All existing unit tests continue to pass after ISP abstraction | Test inventory complete; all 26 tests confirmed isolated from ISP layer |
</phase_requirements>

---

## Summary

Phase 1 is a refactoring exercise, not a feature build. The goal is to extract Twilio-specific I/O from `conversation.py` and `agent.py` into a `TwilioISP` class, define an `ISP` Protocol both implementations satisfy, and build `LocalISP` that routes audio in-process. No new user-visible behavior is added.

The good news: the existing architecture is clean. The pure state machine (`state.py`, `types.py`) already has zero Twilio coupling — it works entirely with domain events. The 26 existing unit tests test only `state.py` and the conversation loop at a high level; none of them import Twilio or touch WebSocket code directly. They will pass unchanged after the refactor.

The coupling to eliminate is concentrated in exactly four places: (1) `conversation.py`'s `read_twilio()` inner function and the `websocket` parameter it closes over, (2) `agent.py`'s `websocket` constructor parameter (used only by `AudioPlayer` and `inject_dtmf`), (3) `player.py`'s `_websocket` reference (used to call `send_text` for audio and clear messages), and (4) `server.py`'s `on_dtmf` and `on_hangup` callbacks that perform Twilio REST calls inline.

**Primary recommendation:** Introduce `TwilioISP` and `LocalISP` as concrete classes satisfying an `ISP` Protocol. Rename `run_conversation_over_twilio()` to `run_conversation()` accepting an ISP parameter. Keep the function shape — don't make it a class. Use an explicit `connect(peer)` method for LocalISP pairing.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `typing.Protocol` | stdlib (3.8+) | ISP interface definition | Decided; structural typing; no import needed |
| `asyncio.Queue` | stdlib | LocalISP audio routing between peers | Already used throughout codebase |
| `asyncio` | stdlib | Background tasks, task cancellation | Already the async model in use |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `twilio.rest.Client` | already in requirements.txt | TwilioISP DTMF REST redirect | TwilioISP.send_dtmf() |
| `numpy`, `audioop`/`audioop_lts` | already in requirements.txt | DTMF tone generation in dtmf.py | TwilioISP.send_dtmf() calls this existing utility |
| `pytest-asyncio` | already installed | Async test support | LocalISP tests need async fixtures |

### No New Dependencies Required
The entire phase is achievable with stdlib asyncio, typing.Protocol, and the existing Twilio SDK already in requirements.txt. No pip installs needed.

---

## Architecture Patterns

### Recommended Project Structure

```
shuo/shuo/
├── types.py           # unchanged — events/actions stay as-is
├── state.py           # unchanged — pure function, no ISP coupling
├── conversation.py    # rename run_conversation_over_twilio -> run_conversation
│                      # replace: websocket param -> isp param
│                      # remove: read_twilio(), parse_twilio_message() import
├── agent.py           # replace: websocket param -> isp param
│                      # AudioPlayer and inject_dtmf use isp
├── server.py          # /ws handler creates TwilioISP, calls run_conversation
└── services/
    ├── player.py      # replace: WebSocket ref -> ISP ref; send_text -> send_audio
    ├── twilio_isp.py  # NEW: TwilioISP class (moves code from conversation.py, server.py)
    ├── local_isp.py   # NEW: LocalISP class
    ├── isp.py         # NEW: ISP Protocol definition
    ├── dtmf.py        # unchanged — pure tone generator, stays as utility
    └── twilio_client.py # reduced: make_outbound_call stays; parse_twilio_message moves to TwilioISP
```

### Pattern 1: ISP Protocol (typing.Protocol — structural typing)

```python
# shuo/shuo/services/isp.py
# Source: Python docs — typing.Protocol for structural subtyping
from typing import Protocol, Callable, Awaitable

class ISP(Protocol):
    async def start(
        self,
        on_media: Callable[[bytes], Awaitable[None]],
        on_start: Callable[[str, str, str], Awaitable[None]],  # stream_sid, call_sid, phone
        on_stop: Callable[[], Awaitable[None]],
    ) -> None: ...

    async def stop(self) -> None: ...

    async def send_audio(self, payload: str) -> None: ...
    """payload is base64-encoded μ-law, same as AudioPlayer chunks."""

    async def send_dtmf(self, digit: str) -> None: ...

    async def hangup(self) -> None: ...

    async def call(self, phone: str, twiml_url: str) -> None: ...
```

**Why Protocol (not ABC):** The project already avoids ABCs (confirmed in CONTEXT.md and CONVENTIONS.md). Protocol allows TwilioISP and LocalISP to be passed wherever ISP is expected without explicit inheritance. The planner does not need to add `(ISP)` to class definitions — structural typing handles it.

**Callback signatures for `start()`:** The FluxService model uses `on_end_of_turn(transcript)` and `on_start_of_turn()` at constructor time. The ISP decision uses `start(on_media, on_start, on_stop)` callbacks registered at start-time instead. This is a deliberate difference: ISP callbacks include stream lifecycle (on_start, on_stop) that FluxService doesn't need. Keep this distinction — don't conflate the two.

**on_start signature:** Currently `StreamStartEvent` carries `stream_sid`, `call_sid`, and `phone`. The `on_start` callback must carry all three. Suggested: `on_start(stream_sid: str, call_sid: str, phone: str)` as positional args (matches the dataclass fields).

**send_audio payload type:** The existing `AudioPlayer._send_audio()` sends base64-encoded strings (not raw bytes) to Twilio. The interface should accept `str` (base64 payload) to match the existing AudioPlayer.send_chunk() input — keep it consistent. The name `send_audio(payload: str)` aligns with the base64 encoding convention throughout the codebase.

### Pattern 2: TwilioISP — Absorbs the Twilio WebSocket reader

**What moves in:**
- `read_twilio()` inner function from `conversation.py` (the while loop that calls `websocket.receive_text()` and calls `parse_twilio_message()`)
- `parse_twilio_message()` from `twilio_client.py` (or TwilioISP calls it internally; either way it is no longer imported by conversation.py)
- `on_dtmf` logic from `server.py`/`websocket_endpoint` (the DTMF REST redirect; moves to `TwilioISP.send_dtmf()`)
- `on_hangup` logic from `server.py` (the Twilio REST hangup; moves to `TwilioISP.hangup()`)
- `make_outbound_call()` call site (moves to `TwilioISP.call()`)

**What stays in server.py:**
- WebSocket accept, `_active_calls` counter, `_draining` guard
- Dashboard observer, registry, bus wiring
- `TwilioISP` construction: `isp = TwilioISP(websocket, call_sid_getter=..., dtmf_pending=_dtmf_pending, ...)`
- Call to `run_conversation(isp, ...)`

**TwilioISP constructor needs:**
- `websocket: WebSocket` — the raw FastAPI WebSocket
- `_dtmf_pending` dict reference — for DTMF reconnect state (Phase 2 moves this to a lock; Phase 1 just passes it in)
- Access to env vars (TWILIO_ACCOUNT_SID, AUTH_TOKEN, PUBLIC_URL) — can read directly from os.getenv inside methods

**Important: send_clear on barge-in.** Currently `AudioPlayer.stop_and_clear()` sends a Twilio-specific `{"event": "clear", "streamSid": ...}` message directly via `websocket.send_text()`. After the refactor, this becomes `isp.send_clear()` (a separate method) or is folded into `isp.send_audio()` design. Recommendation: add a `send_clear()` method to the ISP protocol, or treat it as part of `stop()`. See "Outbound send_clear" pitfall below.

### Pattern 3: LocalISP — In-process peer routing

**Recommended pairing design: explicit `connect(peer: LocalISP)` method**

Rationale:
- A factory function `make_local_pair()` hides the objects and makes testing harder — you can't inspect each ISP independently.
- A shared session object is a third class that both ISPs depend on — adds indirection without benefit.
- `connect(peer)` is explicit and easy to read: `a.connect(b); b.connect(a)` or a factory `pair_local_isps(a, b)`.
- LocalISP can be constructed without its peer and connected later, which is useful in tests.

```python
# Usage pattern
a = LocalISP()
b = LocalISP()
a.connect(b)
b.connect(a)
# or: LocalISP.pair(a, b)  — class method that does the above two lines
```

**How inbound audio flows (LocalISP):**
1. `b.start(on_media, on_start, on_stop)` — registers callbacks on LocalISP `b`
2. `a.send_audio(payload)` — puts payload into `b`'s inbound queue
3. `b`'s background task reads from its queue, calls `b._on_media(payload_as_bytes)`
4. conversation loop on agent `b` receives a `MediaEvent` via the callback

**LocalISP internal queue:** `asyncio.Queue()` with no maxsize (unbounded). Audio is real-time; backpressure isn't needed for in-process simulation. If a maxsize is desired for testing resource limits, 0 (unbounded) is the safe default.

**on_start for LocalISP:** When `a.call()` is called (or when `a.start()` is called — since `call()` is a no-op for LocalISP), `a` should signal `b`'s `on_start` callback with synthetic `stream_sid`, `call_sid`, and `phone` values (e.g., `"local-{uuid}"`, `"local-call"`, `"local"`).

**DTMFToneEvent in LocalISP:** `LocalISP.send_dtmf(digit)` must push a `DTMFToneEvent(digits=digit)` directly into the peer's event queue. The peer's conversation loop handles it via the `on_dtmf` dispatch in `conversation.py`. This means `LocalISP` needs a reference to the peer's event queue OR calls the peer's `on_media`/callbacks mechanism. Simpler: LocalISP exposes an `_inject_event(event)` method; `send_dtmf` calls `peer._inject_event(DTMFToneEvent(digits=digit))`.

**hangup for LocalISP:** `LocalISP.hangup()` calls `peer._on_stop()` which triggers `StreamStopEvent` delivery, causing the peer's conversation loop to exit cleanly.

### Pattern 4: VoiceSession shape — function, not class

**Recommendation: Keep `run_conversation()` as an async function**

- The existing `run_conversation_over_twilio()` is a 285-line async function. It works. The refactor just replaces `websocket` with `isp` and removes the inner `read_twilio()` function.
- Converting to a class would require converting ~20 inner functions and closures to methods. High churn, no benefit.
- The function signature remains compatible with the server.py call site after replacing `websocket` with `isp`.

**New signature:**
```python
async def run_conversation(
    isp: ISP,
    observer: Optional[Callable[[dict], None]] = None,
    should_suppress_agent: Optional[Callable[[], bool]] = None,
    on_agent_ready: Optional[Callable[["Agent"], None]] = None,
    get_goal: Optional[Callable[[str], str]] = None,
    on_hangup: Optional[Callable[[], None]] = None,   # may become isp.hangup()
    get_saved_state: Optional[Callable[[str], Optional[dict]]] = None,
    tts_pool: Optional[TTSPool] = None,
    flux_pool: Optional[FluxPool] = None,
    ivr_mode: Optional[Callable[[], bool]] = None,
    on_dtmf: Optional[Callable[[str], None]] = None,  # may become isp.send_dtmf()
) -> None:
```

Note: `on_hangup` and `on_dtmf` callbacks currently live in `server.py` and contain the Twilio REST logic. After the refactor, these move into `TwilioISP`. The question is whether to remove them from `run_conversation()` entirely (and call `isp.hangup()` / `isp.send_dtmf()` directly) or keep them as optional overrides. **Recommendation:** Call `isp.hangup()` and `isp.send_dtmf()` directly from within `run_conversation()` — drop `on_hangup` and `on_dtmf` parameters. This simplifies the ISP contract and removes callback indirection.

### Pattern 5: AudioPlayer change

**Current:** `AudioPlayer.__init__(websocket, stream_sid, on_done)`
**After:** `AudioPlayer.__init__(isp, stream_sid, on_done)`

Internal changes:
- `_send_audio(payload)` → `await self._isp.send_audio(payload)` (drops the JSON formatting — TwilioISP does it)
- `_send_clear()` → `await self._isp.send_clear()` (new ISP method, or fold into send_audio with a clear sentinel)

**Recommendation:** Add `send_clear()` to the ISP interface. TwilioISP sends the Twilio `{"event": "clear", ...}` message. LocalISP no-ops it (no buffer to clear in-process). This keeps `AudioPlayer` clean.

**ISP Protocol update with send_clear:**
```python
async def send_clear(self) -> None: ...  # Flush remote audio buffer
```

**Updated ISP member count:** 7 methods total (`start`, `stop`, `send_audio`, `send_clear`, `send_dtmf`, `hangup`, `call`). The `send_clear` addition is required to complete the AudioPlayer refactor cleanly.

### Anti-Patterns to Avoid

- **Passing both `websocket` and `isp` transitionally:** Do not add ISP as a second parameter while keeping `websocket`. Replace in a single pass.
- **Making LocalISP.send_audio synchronous:** It must be `async def` to satisfy the Protocol and allow `await` at call sites.
- **Sharing a single `asyncio.Queue` between both LocalISP peers:** Each peer gets its own inbound queue. `send_audio` on A puts into B's queue; `send_audio` on B puts into A's queue.
- **Calling `parse_twilio_message()` in conversation.py after refactor:** This import must be removed from conversation.py. It belongs inside TwilioISP.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Async inter-task signaling | Custom event/semaphore | `asyncio.Queue` | Already pattern in codebase; handles backpressure, cancellation correctly |
| Structural type checking | Abstract base class with register() | `typing.Protocol` | Decided; duck typing is sufficient; no isinstance checks needed |
| Twilio REST client | HTTP calls via httpx | `twilio.rest.Client` (already in requirements.txt) | SDK handles auth, retries, and E.164 format validation |
| DTMF audio generation | Raw numpy DSP | `dtmf.py:generate_dtmf_ulaw_b64()` | Already implemented and tested; TwilioISP calls this unchanged |
| Background reader task | Thread-based reader | `asyncio.create_task()` | Consistent with all other background tasks in the codebase |

---

## Common Pitfalls

### Pitfall 1: send_clear is a hidden coupling point

**What goes wrong:** `AudioPlayer.stop_and_clear()` calls `_send_clear()` which sends `{"event": "clear", "streamSid": ...}` — a Twilio-specific protocol message. If `send_clear()` is not added to the ISP interface, the refactor is incomplete and `AudioPlayer` still imports WebSocket-specific logic.
**Why it happens:** The "clear" message is easy to miss because it's not part of the outbound audio path — it only fires on barge-in.
**How to avoid:** Add `send_clear()` to the ISP Protocol explicitly. TwilioISP sends the clear message. LocalISP no-ops it.
**Warning signs:** AudioPlayer still has `from fastapi import WebSocket` in its imports after refactor.

### Pitfall 2: inject_dtmf in Agent uses WebSocket directly

**What goes wrong:** `Agent.inject_dtmf()` (line 290-303 in agent.py) constructs a Twilio media message and sends it directly via `self._websocket.send_text()`. This is separate from `AudioPlayer` and easy to miss during refactor.
**Why it happens:** `inject_dtmf` is used by the dashboard for supervisor DTMF injection, not by the DTMF marker flow. It bypasses `AudioPlayer`.
**How to avoid:** `Agent.inject_dtmf()` becomes `await self._isp.send_audio(audio)` — the same interface. The websocket constructor parameter on Agent disappears.
**Warning signs:** `agent.py` still imports `from fastapi import WebSocket` after refactor.

### Pitfall 3: conversation.py closes over `websocket` in `read_twilio()`

**What goes wrong:** The inner function `read_twilio()` in `conversation.py` closes over the `websocket` parameter. When removing the parameter, `read_twilio()` must move into `TwilioISP` (as `TwilioISP.start()` background task) or be removed from `conversation.py` entirely.
**Why it happens:** Inner functions are invisible at the call site; easy to miss during a parameter rename.
**How to avoid:** After removing `websocket` from `run_conversation()`, verify there are no remaining `websocket` references in the function body.
**Warning signs:** `NameError: name 'websocket' is not defined` at test time.

### Pitfall 4: on_hangup and on_dtmf in conversation.py — two ways to dispatch

**What goes wrong:** Both `on_hangup` and `on_dtmf` are currently passed in from `server.py` as callbacks. After moving the Twilio REST logic into TwilioISP, if the callbacks remain, `run_conversation()` may call `on_hangup()` (old path) AND `isp.hangup()` (new path), double-executing.
**Why it happens:** Parameters left in the function signature as dead code that still gets wired up.
**How to avoid:** Drop `on_hangup` and `on_dtmf` parameters from `run_conversation()` entirely. Replace their call sites with `await isp.hangup()` and `await isp.send_dtmf(digits)` directly.
**Warning signs:** Two Twilio REST calls made per DTMF event, or Twilio double-hangup error in logs.

### Pitfall 5: LocalISP.on_start timing

**What goes wrong:** LocalISP's `start()` registers callbacks but never fires `on_start` — so `run_conversation()` never creates the Agent (it waits for `StreamStartEvent` which comes from `on_start`).
**Why it happens:** `TwilioISP.on_start` fires when Twilio sends a `{"event": "start"}` message. There's no equivalent trigger in LocalISP.
**How to avoid:** `LocalISP.start()` must fire `on_start` from its background task shortly after startup (or `LocalISP.call()` triggers the peer's `on_start`). The simplest approach: after starting the background task in `LocalISP.start()`, immediately enqueue a synthetic `on_start` call with a generated `stream_sid`.
**Warning signs:** `run_conversation()` with LocalISP blocks indefinitely waiting for the Agent to be created.

### Pitfall 6: Thread-safety of asyncio.Queue across tasks

**What goes wrong:** LocalISP runs the peer's audio into a queue from one coroutine context. If `send_audio` is called from a non-async context (e.g., `put_nowait`) while the queue reader is running in another task, items may be dropped or the queue may raise.
**Why it happens:** `asyncio.Queue` is safe across coroutines in the same event loop, but `put_nowait` on a full bounded queue raises. Using unbounded queues (maxsize=0) avoids this.
**How to avoid:** Use `await queue.put(item)` in `send_audio` (not `put_nowait`), and keep queues unbounded.
**Warning signs:** `asyncio.QueueFull` in LocalISP tests.

### Pitfall 7: IVR barge-in test (`test_ivr_barge_in.py`) is tightly coupled to `run_conversation_over_twilio`

**What goes wrong:** `test_ivr_barge_in.py` patches `shuo.conversation.Agent` and calls `run_conversation_over_twilio()` with a `MockWebSocket`. After renaming the function and changing its WebSocket parameter to an ISP parameter, the test will fail at the call site.
**Why it happens:** The test imports and calls `run_conversation_over_twilio` by name, and passes a `MockWebSocket` as the first arg.
**How to avoid:** Update `test_ivr_barge_in.py` to call `run_conversation(isp=..., ...)` using a `MockISP` or `LocalISP`. The test logic (barge-in suppression) is unchanged — only the setup changes. The 24 tests in `test_update.py` are unaffected (they test `process_event` directly).
**Warning signs:** `TypeError` on `run_conversation_over_twilio()` signature after rename.

---

## Code Examples

### Verified: FluxService start() pattern (model for ISP.start())

```python
# Source: shuo/shuo/services/flux.py — FluxService.__init__ and start()
# FluxService registers callbacks at construction time (not start time).
# ISP decision: register at start() time instead. Both patterns work.

class FluxService:
    def __init__(
        self,
        on_end_of_turn: Callable[[str], Awaitable[None]],
        on_start_of_turn: Callable[[], Awaitable[None]],
    ):
        self._on_end_of_turn = on_end_of_turn
        self._on_start_of_turn = on_start_of_turn

    async def start(self) -> None:
        # ... connects, spawns background listener_task
        self._listener_task = asyncio.create_task(
            self._connection.start_listening()
        )
```

**Difference for ISP:** callbacks are passed to `start()`, not `__init__()`. This matches the CONTEXT.md decision.

### Verified: AudioPlayer._send_audio() — the exact method that changes

```python
# Source: shuo/shuo/services/player.py lines 148-157
async def _send_audio(self, payload: str) -> None:
    """Send a single audio chunk to Twilio."""
    message = {
        "event": "media",
        "streamSid": self._stream_sid,
        "media": {
            "payload": payload
        }
    }
    await self._websocket.send_text(json.dumps(message))
```

**After refactor:**
```python
async def _send_audio(self, payload: str) -> None:
    await self._isp.send_audio(payload)
```

The JSON formatting moves into `TwilioISP.send_audio()`. `LocalISP.send_audio()` puts raw bytes into peer's queue (decode the base64 payload to bytes before queuing, since Flux expects bytes).

### Verified: AudioPlayer._send_clear() — the hidden coupling point

```python
# Source: shuo/shuo/services/player.py lines 159-165
async def _send_clear(self) -> None:
    """Send clear message to Twilio to flush audio buffer."""
    message = {
        "event": "clear",
        "streamSid": self._stream_sid
    }
    await self._websocket.send_text(json.dumps(message))
```

**After refactor:**
```python
async def _send_clear(self) -> None:
    await self._isp.send_clear()
```

### Verified: Agent constructor — current websocket reference

```python
# Source: shuo/shuo/agent.py lines 127-150
class Agent:
    def __init__(
        self,
        websocket: WebSocket,   # <-- becomes isp: ISP
        stream_sid: str,
        emit: Callable[[Any], None],
        tts_pool: TTSPool,
        tracer: Tracer,
        goal: str = "",
        on_token_observed: Optional[Callable[[str], None]] = None,
    ):
        self._websocket = websocket  # <-- becomes self._isp = isp
```

Used in Agent at:
1. `AudioPlayer(websocket=self._websocket, ...)` → `AudioPlayer(isp=self._isp, ...)`
2. `inject_dtmf()` — `await self._websocket.send_text(msg)` → `await self._isp.send_audio(audio)`

### Verified: conversation.py Agent construction — the injection point

```python
# Source: shuo/shuo/conversation.py lines 145-156
agent = Agent(
    websocket=websocket,      # <-- becomes isp=isp
    stream_sid=event.stream_sid,
    emit=lambda e: event_queue.put_nowait(e),
    tts_pool=tts_pool,
    tracer=tracer,
    goal=goal,
    ...
)
```

### Verified: server.py on_dtmf — moves to TwilioISP.send_dtmf()

```python
# Source: shuo/shuo/server.py lines 633-667
def on_dtmf(digits: str) -> None:
    """Save agent history and redirect the call to play DTMF via REST API."""
    c = dashboard_registry.get(ctx["call_id"])
    # ... REST redirect to /twiml/ivr-dtmf?digit=...
    async def _do_dtmf():
        _dtmf_pending[call_sid] = { ... }
        await loop.run_in_executor(
            None, lambda: client.calls(call_sid).update(url=dtmf_url, method="POST")
        )
    asyncio.create_task(_do_dtmf())
```

This logic moves into `TwilioISP.send_dtmf()`. TwilioISP needs to receive the `call_sid` — this comes from the Twilio `{"event": "start"}` message it will parse internally.

### Verified: server.py on_hangup — moves to TwilioISP.hangup()

```python
# Source: shuo/shuo/server.py lines 599-619
def on_hangup():
    c = dashboard_registry.get(ctx["call_id"])
    call_sid = c.call_sid
    async def _do_hangup():
        await loop.run_in_executor(
            None, lambda: client.calls(call_sid).update(status="completed")
        )
    return asyncio.create_task(_do_hangup())
```

Note: `dashboard_registry` access inside `TwilioISP.hangup()` would be a dependency. To avoid coupling TwilioISP to dashboard internals, pass `call_sid` into TwilioISP at construction time (or have it read from the parsed start event). TwilioISP then only needs `call_sid` + env vars to perform the hangup.

---

## Existing Tests — Full Inventory

### 26 Tests Confirmed Passing (2026-03-21)

**`shuo/tests/test_update.py` — 24 tests, all pure state machine:**

| Class | Tests | What They Test |
|-------|-------|---------------|
| `TestStreamLifecycle` | 4 | `StreamStartEvent` sets stream_sid, resets phase; `StreamStopEvent` triggers ResetAgentTurnAction when RESPONDING |
| `TestMediaRouting` | 3 | `MediaEvent` always produces `FeedFluxAction`; does not change state |
| `TestFluxEndOfTurn` | 3 | Non-empty transcript → RESPONDING + StartAgentTurnAction; empty → ignored; ignored if already RESPONDING |
| `TestFluxStartOfTurn` | 2 | RESPONDING → barge-in resets to LISTENING; ignored if LISTENING |
| `TestAgentTurnDone` | 2 | RESPONDING → LISTENING; ignored if LISTENING |
| `TestCompleteFlow` | 5 | Multi-step flows: full turn, interrupt, multi-turn, audio forwarding |
| `TestEdgeCases` | 5 | Immutability, safe no-ops, edge state combinations |

**These 24 tests are guaranteed safe:** They import only `shuo.types` and `shuo.state`. Neither module has any Twilio/WebSocket coupling and neither will be changed by the ISP refactor. Zero risk of breakage.

**`shuo/tests/test_ivr_barge_in.py` — 2 tests, integration-level:**

| Test | What It Tests | ISP Impact |
|------|--------------|------------|
| `test_ivr_barge_in_suppressed` | `run_conversation_over_twilio()` with `MockWebSocket` and `ivr_mode=True`; barge-in suppressed | BREAKING — must be updated to use ISP signature |
| `test_normal_mode_barge_in_still_works` | Same but `ivr_mode=False`; barge-in fires `cancel_turn` | BREAKING — must be updated to use ISP signature |

These tests patch `shuo.conversation.Agent` and call `run_conversation_over_twilio()` directly. After renaming to `run_conversation(isp=...)`, both tests must be updated. The test logic (asserting cancel_turn behavior) remains valid — only the call site and setup change. A `MockISP` (or inline LocalISP) replaces `MockWebSocket` in these tests.

---

## LocalISP Pairing Design — Recommendation

**Use `LocalISP.pair(a, b)` class method:**

```python
class LocalISP:
    def __init__(self) -> None:
        self._peer: Optional["LocalISP"] = None
        self._on_media: Optional[Callable] = None
        self._on_start: Optional[Callable] = None
        self._on_stop: Optional[Callable] = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None

    @classmethod
    def pair(cls, a: "LocalISP", b: "LocalISP") -> None:
        a._peer = b
        b._peer = a

    async def start(self, on_media, on_start, on_stop) -> None:
        self._on_media = on_media
        self._on_start = on_start
        self._on_stop = on_stop
        self._task = asyncio.create_task(self._reader())
        # Fire synthetic stream start immediately
        stream_sid = f"local-{uuid.uuid4().hex[:8]}"
        await on_start(stream_sid, "local-call-sid", "local")

    async def _reader(self) -> None:
        """Read from queue, call on_media for each item."""
        while True:
            payload_bytes = await self._queue.get()
            if payload_bytes is None:  # Sentinel for stop
                break
            await self._on_media(payload_bytes)

    async def send_audio(self, payload: str) -> None:
        """Send to peer's inbound queue (base64 -> bytes)."""
        if self._peer and self._peer._queue is not None:
            audio_bytes = base64.b64decode(payload)
            await self._peer._queue.put(audio_bytes)

    async def send_clear(self) -> None:
        pass  # No-op for in-process

    async def send_dtmf(self, digit: str) -> None:
        """Deliver DTMFToneEvent directly to peer's queue."""
        if self._peer and self._peer._inject_event:
            await self._peer._inject_event(DTMFToneEvent(digits=digit))

    async def hangup(self) -> None:
        """Signal peer's stop callback."""
        if self._peer and self._peer._on_stop:
            await self._peer._on_stop()

    async def stop(self) -> None:
        if self._task:
            await self._queue.put(None)  # Sentinel
            await self._task

    async def call(self, phone: str, twiml_url: str) -> None:
        pass  # Pairing happens at construction time
```

**DTMFToneEvent injection mechanism:** `run_conversation()` will need to expose a way for LocalISP to inject events into its queue. The cleanest approach: `run_conversation()` passes a callable `inject_event` into LocalISP when creating the ISP or after ISP start, via a separate `set_inject(fn)` method. Alternatively, LocalISP can take the queue as a constructor parameter (simpler but less clean). **Recommendation:** Add an `_inject` callable attribute set by the conversation loop after ISP start via `isp._inject = event_queue.put_nowait`. This is a private convention, not part of the Protocol.

---

## VoiceSession — Final Shape Recommendation

**Keep as a function, rename it:**

```python
# shuo/shuo/conversation.py

async def run_conversation(
    isp: "ISP",
    observer: Optional[Callable[[dict], None]] = None,
    should_suppress_agent: Optional[Callable[[], bool]] = None,
    on_agent_ready: Optional[Callable[["Agent"], None]] = None,
    get_goal: Optional[Callable[[str], str]] = None,
    get_saved_state: Optional[Callable[[str], Optional[dict]]] = None,
    tts_pool: Optional[TTSPool] = None,
    flux_pool: Optional[FluxPool] = None,
    ivr_mode: Optional[Callable[[], bool]] = None,
) -> None:
```

**Removed from old signature:** `websocket`, `on_hangup`, `on_dtmf` (moved to TwilioISP).
**Kept:** All dashboard observer/registry wiring parameters (unchanged — server.py still provides these).

The old name `run_conversation_over_twilio` should be kept as a deprecated alias that raises a deprecation warning or just removed. Since nothing outside `server.py` calls it, outright removal is fine.

---

## Async/Threading Risk Analysis

### Risk 1: ISP callbacks called from TwilioISP's background task

**TwilioISP.start()** spawns a background task that reads from the Twilio WebSocket and calls `on_media()`, `on_start()`, `on_stop()`. These callbacks are `async` and run as coroutines awaited from within the task. This is the same pattern as FluxService. **Risk: LOW.** All async, same event loop, no thread boundary.

### Risk 2: LocalISP queue reader and send_audio in different tasks

`LocalISP._reader()` runs as a background task. `send_audio()` calls `await peer._queue.put()` from the conversation loop's event dispatch. Both are coroutines in the same asyncio event loop. **Risk: LOW.** `asyncio.Queue` is coroutine-safe within a single event loop.

### Risk 3: DTMFToneEvent injection into conversation loop queue

`LocalISP.send_dtmf()` will call `peer._inject(DTMFToneEvent(...))` which is `event_queue.put_nowait()`. `put_nowait` is safe from any coroutine in the same event loop. `put_nowait` on an unbounded queue never raises. **Risk: LOW.**

### Risk 4: TwilioISP receives DTMF REST logic from server.py — asyncio.get_event_loop()

The current `on_dtmf` in `server.py` uses `asyncio.get_event_loop()` (line 646). This is deprecated in Python 3.10+ (use `asyncio.get_running_loop()` instead). When this moves into TwilioISP, use `asyncio.get_running_loop()` or `loop.run_in_executor()` from within an async method context. **Risk: LOW** (cleanup opportunity, not a blocker).

### Risk 5: HangupRequestEvent dispatch in conversation.py calls `isp.hangup()`

Currently `HangupRequestEvent` triggers `on_hangup()` which returns a coroutine/future that is awaited inline. After moving to `await isp.hangup()`, this is a direct await inside the event loop. No risk, but `isp.hangup()` must not block the event loop (it uses `run_in_executor` for the Twilio REST call). **Risk: LOW** if implemented correctly with `run_in_executor`.

### Risk 6: Queue maxsize for LocalISP

If a future test creates a LocalISP with a bounded queue and `send_audio` blocks, the conversation loop could deadlock. Using `maxsize=0` (unbounded) avoids this. For Phase 1, use unbounded. **Risk: LOW** if unbounded queues are used.

---

## File Layout Recommendation

```
shuo/shuo/services/
├── isp.py          # NEW — ISP Protocol definition only (import-safe, no deps)
├── twilio_isp.py   # NEW — TwilioISP class
├── local_isp.py    # NEW — LocalISP class
├── player.py       # MODIFIED — WebSocket ref -> ISP ref
├── twilio_client.py # REDUCED — parse_twilio_message() moves to twilio_isp.py
│                    #           make_outbound_call() stays (still useful standalone)
└── dtmf.py         # UNCHANGED — pure utility
```

```
shuo/shuo/
├── conversation.py  # MODIFIED — rename, replace websocket param with isp
└── agent.py         # MODIFIED — replace websocket param with isp
```

```
shuo/tests/
├── test_update.py          # UNCHANGED (zero ISP coupling)
├── test_ivr_barge_in.py    # UPDATED — replace MockWebSocket with MockISP/LocalISP
└── test_isp.py             # NEW — LocalISP pairing, send_audio routing, DTMF delivery
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `asyncio.get_event_loop()` | `asyncio.get_running_loop()` | Python 3.10 | Use get_running_loop() in TwilioISP for run_in_executor calls |
| `audioop` (stdlib) | `audioop_lts` (PyPI) for Python 3.13+ | Python 3.13 | dtmf.py already handles this with try/except import |
| `@app.on_event("startup")` | `@app.lifespan` context manager | FastAPI 0.93+ | server.py uses deprecated on_event; don't change in Phase 1 |

---

## Open Questions

1. **Should `parse_twilio_message()` be deleted or kept in twilio_client.py?**
   - What we know: It currently lives in `twilio_client.py` and is imported by both `conversation.py` and `server.py`. After refactor, conversation.py no longer needs it.
   - What's unclear: `server.py` still imports it for the `/ws-listen` route (line 29 `from .services.twilio_client import make_outbound_call, parse_twilio_message`). But `/ws-listen` uses raw JSON parsing, not `parse_twilio_message`.
   - Recommendation: Keep `parse_twilio_message` in `twilio_client.py` for now; TwilioISP can call it. Remove the server.py import if unused.

2. **Does `inject_dtmf()` in Agent move to ISP or stay on Agent?**
   - What we know: It's called by the dashboard (`dashboard/server.py` — currently references `agent.inject_dtmf(digit)` if the feature exists). It sends raw audio to Twilio.
   - What's unclear: Whether the dashboard directly calls `agent.inject_dtmf()` in current code.
   - Recommendation: Keep `inject_dtmf()` on Agent but change it to call `await self._isp.send_audio(audio)`. The Agent continues to own the DTMF tone generation.

3. **How does TwilioISP pass `call_sid` to `hangup()` and `send_dtmf()`?**
   - What we know: `call_sid` arrives in the Twilio `{"event": "start"}` message parsed by the background reader.
   - Recommendation: TwilioISP stores `self._call_sid` when it processes the start event; `hangup()` and `send_dtmf()` use it. No need to pass it externally.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (7.0.0+) with pytest-asyncio |
| Config file | None detected — pytest defaults |
| Quick run command | `python3 -m pytest shuo/tests/ -q --tb=short` |
| Full suite command | `python3 -m pytest shuo/tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ISP-01 | ISP Protocol has all 6 (+send_clear = 7) required methods | unit | `pytest shuo/tests/test_isp.py::test_protocol_shape -x` | ❌ Wave 0 |
| ISP-02 | TwilioISP satisfies ISP Protocol; real Twilio behavior unchanged | unit | `pytest shuo/tests/test_isp.py::test_twilio_isp_satisfies_protocol -x` | ❌ Wave 0 |
| ISP-02 | TwilioISP.send_audio formats Twilio media message correctly | unit | `pytest shuo/tests/test_isp.py::test_twilio_isp_send_audio -x` | ❌ Wave 0 |
| ISP-03 | LocalISP: audio written by A is readable by B | unit | `pytest shuo/tests/test_isp.py::test_local_isp_audio_routing -x` | ❌ Wave 0 |
| ISP-03 | LocalISP: DTMF from A delivers DTMFToneEvent to B's queue | unit | `pytest shuo/tests/test_isp.py::test_local_isp_dtmf -x` | ❌ Wave 0 |
| ISP-03 | LocalISP: hangup on A fires on_stop on B | unit | `pytest shuo/tests/test_isp.py::test_local_isp_hangup -x` | ❌ Wave 0 |
| ISP-04 | run_conversation() accepts LocalISP without error | integration | `pytest shuo/tests/test_ivr_barge_in.py -x` | ✅ (update needed) |
| ISP-04 | run_conversation() accepts TwilioISP (via mock) without error | integration | `pytest shuo/tests/test_ivr_barge_in.py -x` | ✅ (update needed) |
| ISP-05 | All 24 state machine tests pass unchanged | unit | `pytest shuo/tests/test_update.py -q` | ✅ |
| ISP-05 | Both IVR barge-in tests pass after test update | integration | `pytest shuo/tests/test_ivr_barge_in.py -q` | ✅ (update needed) |

### Sampling Rate
- **Per task commit:** `python3 -m pytest shuo/tests/test_update.py -q --tb=short`
- **Per wave merge:** `python3 -m pytest shuo/tests/ -q --tb=short`
- **Phase gate:** All 26 original tests + new ISP tests green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `shuo/tests/test_isp.py` — covers ISP-01, ISP-02 (protocol shape), ISP-03 (LocalISP routing, DTMF, hangup)
- [ ] `shuo/tests/test_ivr_barge_in.py` — update existing 2 tests to use ISP signature (covers ISP-04)

*(No new framework install needed — pytest-asyncio already available)*

---

## Sources

### Primary (HIGH confidence)
- Direct code inspection — `shuo/shuo/conversation.py`, `agent.py`, `server.py`, `services/player.py`, `services/dtmf.py`, `services/flux.py`, `services/twilio_client.py`, `types.py`, `state.py`
- Test execution — `python3 -m pytest shuo/tests/ -q` confirmed 26 tests pass
- `shuo/tests/test_update.py` — full test inventory read and analyzed
- `shuo/tests/test_ivr_barge_in.py` — integration test read; ISP impact assessed

### Secondary (MEDIUM confidence)
- Python `typing.Protocol` documentation — structural subtyping pattern (stdlib, stable since 3.8)
- `asyncio.Queue` thread-safety properties — well-established asyncio behavior

### Tertiary (LOW confidence)
- None — all findings based on direct code inspection

---

## Metadata

**Confidence breakdown:**
- Coupling points in conversation.py: HIGH — read every line
- AudioPlayer send_clear pitfall: HIGH — confirmed in player.py lines 159-165
- Agent inject_dtmf pitfall: HIGH — confirmed in agent.py lines 290-303
- LocalISP pairing design: MEDIUM — recommended approach, not validated against future usage
- Test breakage assessment: HIGH — confirmed which tests use which imports
- Async risk analysis: HIGH — all risks assessed against actual asyncio semantics

**Research date:** 2026-03-21
**Valid until:** 2026-04-21 (stable codebase, no external dependencies introduced)
