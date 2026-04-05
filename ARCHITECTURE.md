# Architecture Diagnosis: voice-agent State Management

> Generated: 2026-04-05  
> Scope: `shuo/` — state machine, agent pipeline, conversation loop, server

---

## 1. Executive Summary

The project has a clean functional core (`process_event` → pure) but as features grew, **four parallel state tracks emerged** that are aware of each other but have no clear ownership boundaries. The core tension: the system claims to be event-driven and pure, but several paths bypass the event system entirely.

---

## 2. State Subjects & Smell Catalogue

### `AppState` (`types.py` / `state.py`)
**Role:** Conversation phase routing (LISTENING / RESPONDING / HANGING_UP), hold mode.

| Smell | Detail |
|---|---|
| `stream_sid` identity pollution | `stream_sid` is connection metadata, set once, never read by any state-machine handler. Carried on every `process_event` call for no reason. |
| `hold_mode` mixed with `phase` | These are different abstraction levels: `phase` describes the turn lifecycle; `hold_mode` is a call-state modifier. They belong in separate structs. |

### `Agent` (`agent.py`)
**Role:** LLM → TTS → Player pipeline lifecycle; conversation history access.

| Smell | Detail |
|---|---|
| Implicit turn state machine | Five fields (`_active`, `_pending_hangup`, `_tts_had_text`, `_current_turn_text`, `_dtmf_queue`) collectively form an unnamed `TurnState` with no type boundary. |
| `_on_llm_done` god-branch | 40-line nested conditional (hold_continue → hold_start/end → hangup → dtmf → text → empty) — highest cognitive load in the codebase, untestable in isolation. |
| Duplicate DTMF state | `LLMTurnContext.dtmf_queue` → copied to `Agent._dtmf_queue` → used in `_on_tts_done`. Data copied twice across abstraction layers. |

### `LLMTurnContext` (`llm.py`)
**Role:** Per-turn side-effect collector for pydantic-ai tool calls.

| Smell | Detail |
|---|---|
| `goal_suffix` in per-turn object | Goal is a per-instance constant (set at `__init__`), not a per-turn side effect. Mixing it into a turn-scoped mutable object pollutes the type's purpose. |
| Mutable shared between layers | Tools (pydantic-ai internals) and `Agent._on_llm_done` both read/write this object across abstraction layers. |

### `run_conversation` closures (`conversation.py`)
**Role:** Main event loop, service lifecycle management.

| Smell | Detail |
|---|---|
| 2× direct state bypass | `state = replace(state, phase=Phase.RESPONDING)` called inline on StreamStart (greeting path, handback path), bypassing `process_event`. Breaks the "single state-change exit" invariant and causes incomplete transition logs. |
| `DTMFToneEvent` not routed through action system | Handled by special-case code (`# DTMF DISPATCH`) outside the main action dispatch, inconsistent with the rest of the architecture. |
| Service lifecycle mixed with business logic | 2,000-character function combines: Flux/TTS pool lifecycle, watchdog management, initial greeting, handback resumption, observer bridging, and event loop. |

### `server.py` global + per-call `ctx` dict
**Role:** HTTP routing, global pools, DTMF reconnect state, takeover reconnect state.

| Smell | Detail |
|---|---|
| Raw dict as shared closure state | `ctx = {"call_id": ..., "ivr_mode": False}` shared by 6 closures (`observer`, `should_suppress_agent`, `on_agent_ready`, `get_goal`, `on_dtmf`, `get_saved_state`). No type, no ownership. |
| `_dtmf_pending` parallel registry | Global `dict` implementing a cross-WebSocket state-transfer protocol (DTMF reconnect), separate from `dashboard_registry`, with no TTL or cleanup alignment. |
| Takeover reconnect embedded in factory | `get_saved_state`'s second branch directly manipulates `dashboard_registry`, destroys a bus, and reassigns `ctx["call_id"]` — a hidden registry mutation inside a conversation factory callback. |

---

## 3. Refactoring Priority

| Priority | Change | Benefit |
|---|---|---|
| **P0** | Extract `TurnOutcome` + `_resolve_turn_outcome()` pure fn | Eliminates `_on_llm_done` god-branch; enables unit tests |
| **P0** | Fix 2× state bypass in `conversation.py` via new events | Restores single-exit-point invariant; complete transition logs |
| **P1** | Remove `goal_suffix` from `LLMTurnContext` | Type purity; `LLMTurnContext` becomes a true per-turn container |
| **P1** | Replace `ctx` dict with `CallSession` dataclass | Type safety; eliminates raw-dict closure sharing |
| **P1** | Remove `stream_sid` from `AppState` | `AppState` models only routing logic |
| **P2** | Merge `_dtmf_pending` into `CallRegistry` | Single source of truth for call reconnect state |
| **P2** | Route `DTMFToneEvent` through action system | Architecture consistency |

---

## 4. Diagram 1 — Current State Model

```mermaid
graph TD
    subgraph "Twilio / ISP Layer"
        TW[Twilio WebSocket]
    end

    subgraph "server.py — God Object"
        WS[websocket_endpoint]
        CTX["ctx dict\n{call_id, ivr_mode}"]
        DTMF_PEND["_dtmf_pending\n(global dict, no TTL)"]
        DASH_REG["dashboard_registry\n(ActiveCall objects)"]
        WS --> CTX
        WS --> DTMF_PEND
        WS --> DASH_REG
    end

    subgraph "conversation.py — Oversized Event Loop"
        EQ[asyncio.Queue]
        APP["AppState\nphase / stream_sid / hold_mode"]
        PE["process_event()\n(pure function)"]
        DISPATCH["dispatch actions\n(side effects)"]
        BYPASS["⚠ Direct state mutation\nstate = replace(state, phase=...)"]
        EQ --> PE
        PE --> APP
        PE --> DISPATCH
        WS -.closure callbacks.-> EQ
        BYPASS -.bypasses PE.-> APP
    end

    subgraph "Agent — Implicit Turn State Machine"
        AG_STATE["5 unnamed flags\n_active / _pending_hangup\n_tts_had_text / _current_turn_text\n_dtmf_queue (copy)"]
        ON_DONE["_on_llm_done()\n40-line nested branch"]
        AG_STATE --> ON_DONE
    end

    subgraph "LLMService"
        HIST["_history"]
        CTX2["LLMTurnContext\ndtmf_queue / hold_* / hangup_pending\n+ goal_suffix ⚠ (wrong layer)"]
        TOOLS["pydantic-ai tools\n(mutate LLMTurnContext)"]
        TOOLS --> CTX2
    end

    TW --> WS
    DISPATCH --> AG_STATE
    ON_DONE --> EQ
    ON_DONE --> CTX2
    CTX2 -.copy dtmf_queue.-> AG_STATE

    style BYPASS fill:#ff6b6b,color:#fff
    style CTX fill:#ffa94d,color:#000
    style DTMF_PEND fill:#ffa94d,color:#000
    style ON_DONE fill:#ff6b6b,color:#fff
    style CTX2 fill:#ffd43b,color:#000
```

---

## 5. Diagram 2 — Refactored State Model

```mermaid
graph LR
    subgraph "Layer 0: Immutable Signal Types"
        EVENTS["Events (frozen dataclasses)"]
        ACTIONS["Actions (frozen dataclasses)"]
        TOUT["TurnOutcome (new)\ndtmf_digits / hold_continue\nemit_hold_start / emit_hold_end\nhangup / has_speech"]
    end

    subgraph "Layer 1: Pure Core"
        APP2["AppState\nphase: Phase\nhold_mode: bool\n(stream_sid removed)"]
        PE2["process_event()\nsingle state-change exit\nhandles InitialGreetingEvent\nHandbackStartEvent"]
        EVENTS --> PE2
        PE2 --> ACTIONS
        PE2 --> APP2
    end

    subgraph "Layer 2: Session Context"
        SESS["CallSession (new)\ncall_id: str\nivr_mode: bool\n(replaces ctx dict)"]
    end

    subgraph "Layer 3: Service Wrappers"
        LLM2["LLMService\nhistory + streaming\n(goal_suffix baked at init)"]
        TTC["LLMTurnContext\ndtmf_queue / hold_* / hangup\n(pure tool side-effects, no goal_suffix)"]
        LLM2 --> TTC
    end

    subgraph "Layer 4: Turn Executor (new)"
        EXEC["_resolve_turn_outcome(ctx, text, tts_had_text)\n→ TurnOutcome\npure function, unit-testable"]
        TTC --> EXEC
        EXEC --> TOUT
    end

    subgraph "Layer 5: Infrastructure"
        REG["CallRegistry\n(unified reconnect state)"]
    end

    SESS --> PE2
    TOUT --> ACTIONS
    ACTIONS --> LLM2

    style TOUT fill:#69db7c,color:#000
    style EXEC fill:#69db7c,color:#000
    style SESS fill:#74c0fc,color:#000
```

---

## 6. Diagram 3 — Target Architecture (Global View)

```mermaid
flowchart TD
    subgraph INFRA ["Infrastructure"]
        TW2[Twilio WebSocket]
        FLUX2[Deepgram Flux]
        EL[ElevenLabs / Kokoro TTS]
    end

    subgraph SERVER ["server.py — Thin HTTP Layer"]
        ROUTE[FastAPI Routes]
        SESS2["CallSession\n(typed, replaces ctx dict)"]
        REG2["CallRegistry\n(unified: DTMF reconnect + takeover)"]
        ROUTE --> SESS2
        ROUTE --> REG2
    end

    subgraph CONV ["conversation.py — Slim Event Loop"]
        EQ2["asyncio.Queue[Event]"]
        PE3["process_event(AppState, Event)\n→ AppState, List[Action]\n(sole state-change exit)"]
        DISPATCH2["dispatch(action)\n(local coroutine, no duplication)"]
        OBS["observer bridge\n(pure read, no mutation)"]
        EQ2 --> PE3
        PE3 --> DISPATCH2
        PE3 --> OBS
    end

    subgraph AGENT_LAYER ["Agent Layer"]
        AG2["Agent\n(no implicit flag fields)"]
        EXEC2["_resolve_turn_outcome()\n(pure fn, testable)"]
        DISPATCH3["_dispatch_outcome()\n(async effects)"]
        AG2 --> EXEC2
        EXEC2 --> DISPATCH3
    end

    subgraph STATE_CORE ["Pure State Core"]
        APP3["AppState\nphase / hold_mode"]
        PHASES["LISTENING ↔ RESPONDING ↔ HANGING_UP\n+ InitialGreetingEvent\n+ HandbackStartEvent"]
        APP3 --- PHASES
    end

    TW2 -->|audio| EQ2
    FLUX2 -->|turn events| EQ2
    DISPATCH3 -->|AgentTurnDoneEvent etc| EQ2

    PE3 --> APP3
    DISPATCH2 --> AG2
    DISPATCH2 --> FLUX2

    AG2 --> EL
    EL -->|audio stream| TW2

    SESS2 -.per-call context.-> CONV
    REG2 -.reconnect lookup.-> SERVER

    style APP3 fill:#2f9e44,color:#fff
    style PE3 fill:#2f9e44,color:#fff
    style EXEC2 fill:#1971c2,color:#fff
    style SESS2 fill:#1971c2,color:#fff
```

---

## 7. Files Changed in This Refactor

| File | Changes |
|---|---|
| `shuo/shuo/types.py` | Add `TurnOutcome`; add `InitialGreetingEvent`, `HandbackStartEvent`; remove `stream_sid` from `AppState` |
| `shuo/shuo/state.py` | Handle `InitialGreetingEvent` + `HandbackStartEvent`; drop `stream_sid` from `StreamStartEvent` path |
| `shuo/shuo/services/llm.py` | Remove `goal_suffix` from `LLMTurnContext`; bake system prompt at init |
| `shuo/shuo/agent.py` | Extract `_resolve_turn_outcome()` pure fn + `_dispatch_outcome()`; slim `_on_llm_done` to 3 lines |
| `shuo/shuo/conversation.py` | Extract local `dispatch()` coroutine; replace 2× `state=replace()` bypasses with proper event routing |
| `shuo/shuo/server.py` | Add `CallSession` dataclass; replace `ctx` dict in `websocket_endpoint` |
