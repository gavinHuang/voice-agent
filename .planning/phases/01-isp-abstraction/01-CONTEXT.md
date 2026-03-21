# Phase 1: ISP Abstraction - Context

**Gathered:** 2026-03-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Decouple VoiceSession from Twilio by defining an `ISP` protocol interface, refactoring the existing Twilio integration into `TwilioISP`, and implementing `LocalISP` for in-process calls. VoiceSession (currently `run_conversation_over_twilio()`) must accept any ISP implementation via dependency injection. All 26 existing unit tests must pass. No scope for CLI, benchmarking, or bug fixes — those are later phases.

</domain>

<decisions>
## Implementation Decisions

### ISP Protocol Interface

Full interface with six members — all from the requirements spec:

```python
class ISP(Protocol):
    async def start(self, on_media, on_start, on_stop): ...
    async def stop(self): ...
    async def send_audio(self, payload: bytes): ...
    async def send_dtmf(self, digit: str): ...
    async def hangup(self): ...
    async def call(self, phone: str, twiml_url: str): ...
```

Use Python `Protocol` (structural typing, `typing.Protocol`) — no ABCs or base classes.

### Inbound audio delivery

ISP owns an internal background task and delivers audio via callbacks — same pattern as `FluxService`:

- `start(on_media, on_start, on_stop)` — registers callbacks and starts the background reader
- `on_media(frame)` — called per inbound audio chunk
- `on_start(stream_sid, metadata)` — called when a call stream begins
- `on_stop()` — called when the stream ends

`conversation.py` registers callbacks at construction time; its event queue and main loop remain structurally unchanged.

### Outbound audio

`AudioPlayer` calls `await isp.send_audio(payload)` per μ-law chunk. `TwilioISP.send_audio()` formats and sends the Twilio media frame over the WebSocket. `LocalISP.send_audio()` puts the frame into the peer agent's inbound queue.

### DTMF

`await isp.send_dtmf(digit)` is on the ISP interface. `TwilioISP` makes the Twilio REST call (current behavior in `dtmf.py`). `LocalISP` pushes a `DTMFToneEvent` directly into the peer agent's event queue — no tone generation needed.

### Call setup and hangup

`call()` and `hangup()` are on the ISP. `TwilioISP.call()` wraps `make_outbound_call()`. `TwilioISP.hangup()` wraps the Twilio REST hangup. For `LocalISP`, `call()` is a no-op (pairing happens at construction time); `hangup()` signals the peer's stop callback.

### Claude's Discretion

- How `LocalISP` instances are paired (pair factory, shared session object, or explicit connect) — Claude decides
- Whether `VoiceSession` becomes a class or stays a function with ISP injected as a parameter
- Where `TwilioISP` lives (`shuo/shuo/services/twilio_isp.py` is a reasonable location)
- How much of `server.py`'s WebSocket route changes — TwilioISP should absorb the Twilio-specific parsing
- `AudioPlayer` internal changes (it currently holds a WebSocket reference; it'll need an ISP reference instead)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### ISP Requirements
- `.planning/REQUIREMENTS.md` §ISP Abstraction — ISP-01 through ISP-05: formal requirements with acceptance criteria

### Existing code to refactor (read before touching)
- `shuo/shuo/conversation.py` — `run_conversation_over_twilio()` is the main loop; ISP replaces its WebSocket parameter
- `shuo/shuo/services/player.py` — `AudioPlayer` currently sends to Twilio WebSocket; will call `isp.send_audio()` instead
- `shuo/shuo/services/dtmf.py` — DTMF tone generation + Twilio REST call; logic moves into `TwilioISP.send_dtmf()`
- `shuo/shuo/services/twilio_client.py` — `parse_twilio_message()` and `make_outbound_call()`; both move into TwilioISP
- `shuo/shuo/services/flux.py` — callback-based pattern (`start(on_end_of_turn, ...)`) is the model for ISP.start()
- `shuo/shuo/types.py` — `MediaEvent`, `StreamStartEvent`, `StreamStopEvent` map to ISP callbacks (on_media, on_start, on_stop)
- `shuo/shuo/server.py` — WebSocket route creates TwilioISP from the WebSocket, injects into VoiceSession

### Test guard
- `shuo/tests/` — All 26 unit tests must pass after refactor; run before and after to verify no regression

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `shuo/shuo/services/flux.py` — FluxService callback pattern is the direct model for ISP.start() signature; reuse the pattern verbatim
- `shuo/shuo/services/dtmf.py` — μ-law DTMF tone generator; TwilioISP.send_dtmf() will use it for real calls

### Established Patterns
- Callback registration at `start()` time (FluxService, TTSPool) — ISP follows this same pattern; don't use constructor injection for callbacks
- `asyncio.Queue` per call for events — conversation.py's queue remains the event bus; ISP callbacks push into it
- `frozen=True` dataclasses for events (`types.py`) — `DTMFToneEvent`, `StreamStartEvent`, `StreamStopEvent` remain unchanged; ISP callbacks emit these same types
- Python `Protocol` (structural typing) — no ABCs, no base classes; duck typing with type hints

### Integration Points
- `shuo/shuo/server.py` WebSocket route (`/ws`) — creates `TwilioISP(websocket)` and passes to VoiceSession
- `shuo/shuo/services/player.py` — switches from `websocket.send_text()` to `await isp.send_audio()`
- `dashboard/registry.py` — registers calls; not affected by ISP refactor
- `shuo/tests/` — existing unit tests target `state.py` and `types.py`; ISP layer sits above these and should not break them

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches for pairing, file layout, and class naming.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 01-isp-abstraction*
*Context gathered: 2026-03-21*
