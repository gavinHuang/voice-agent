# Coding Conventions

**Analysis Date:** 2026-04-06 (updated after greenfield module refactor)

## Naming Patterns

**Files:**
- Module names: lowercase, domain-named (e.g., `call.py`, `speech.py`, `voice.py`, `phone.py`)
- TTS providers: `voice_{provider}.py` (e.g., `voice_elevenlabs.py`, `voice_kokoro.py`)
- Test files: `test_*.py` prefix (e.g., `test_update.py`, `test_ivr.py`)
- No `services/` subdirectory — all modules are flat in `shuo/`

**Functions:**
- snake_case for all functions and methods (e.g., `step()`, `run_call()`, `dial_out()`)
- Private methods: leading underscore prefix (e.g., `_on_llm_token()`, `_handle_sigterm()`)
- Callback functions: `on_*` prefix for event handlers (e.g., `on_audio()`, `on_done()`)
- Async functions: no special prefix, just `async def`

**Variables:**
- snake_case for all variables and attributes (e.g., `call_id`, `voice_pool`, `transcriber`)
- Private attributes: leading underscore (e.g., `self._active`, `self._llm`, `self._voice_pool`)
- Boolean flags: descriptive state name (e.g., `_got_first_token`, `_tts_had_text`)
- Protected module-level state: underscore prefix (e.g., `_active_calls`, `_draining`, `_dtmf_pending`)

**Types & Classes:**
- PascalCase for classes (e.g., `CallState`, `Agent`, `LanguageModel`, `Transcriber`, `VoicePool`, `TwilioPhone`, `LocalPhone`)
- Enum members: UPPER_CASE (e.g., `Phase.LISTENING`, `Phase.RESPONDING`, `Phase.ENDING`)
- Dataclasses: frozen immutable types used for events and state (e.g., `@dataclass(frozen=True) class CallState`)

**Key Renames (from old codebase — do not use old names):**
- `AppState` → `CallState`
- `process_event` → `step`
- `run_conversation` → `run_call`
- `FluxEndOfTurnEvent` → `UserSpokeEvent`
- `FluxStartOfTurnEvent` → `UserSpeakingEvent`
- `AgentTurnDoneEvent` → `AgentDoneEvent`
- `HangupRequestEvent` → `HangupEvent`
- `FeedFluxAction` → `StreamToSTTAction`
- `StartAgentTurnAction` → `StartTurnAction`
- `ResetAgentTurnAction` → `CancelTurnAction`
- `TTSPool` → `VoicePool`
- `FluxService`/`FluxPool` → `Transcriber`/`TranscriberPool`
- `TwilioISP` → `TwilioPhone`
- `LocalISP` → `LocalPhone`
- `make_outbound_call` → `dial_out`
- `Phase.HANGING_UP` → `Phase.ENDING`
- `LLMService` → `LanguageModel`

## Code Style

**Formatting:**
- No explicit formatter configured
- Indentation: 4 spaces (Python standard)
- Line length: implicit ~100-120 characters
- String style: both single and double quotes used

**Docstring Style:**
- Module-level docstrings: triple quotes with description
  ```python
  """
  call.py — Events, actions, state, and the call loop.
  """
  ```
- Function docstrings: concise one-liners for simple functions
  ```python
  def step(state: CallState, event: Event) -> tuple[CallState, list[Action]]:
      """Pure state machine: (State, Event) → (State, Actions)."""
  ```

## Import Organization

**Order:**
1. Standard library imports (`asyncio`, `json`, `os`, `sys`, `time`, `logging`)
2. Third-party imports (`fastapi`, `websockets`, `twilio`, `pydantic_ai`)
3. Relative local imports (from `.agent import Agent`, `from .voice import VoicePool`)

**Deferred imports (circular import prevention):**
- `call.py` → `tracer.py` → `log.py` cannot import from `call.py` at module level
- Solution: lazy imports inside method bodies in `log.py`
- `run_call()` imports `Transcriber` and `Agent` inside the function body
- CLI commands import `shuo.web`, `run_call`, `LocalPhone`, `dial_out` inside function bodies

**Import for monitor/simulator from shuo:**
- `monitor/server.py` imports `from shuo.phone import dial_out` (deferred, inside function body)
- `shuo/web.py` imports `from monitor.server import router`, `from simulator.server import app`

**Example from `shuo/web.py`:**
```python
from .call import run_call
from .phone import TwilioPhone, dial_out
from .speech import Transcriber
from .voice import VoicePool
from monitor.server import router as dashboard_router
from monitor import bus as dashboard_bus, registry as dashboard_registry
```

## Error Handling

**Patterns:**
- Try/except with specific error logging via `get_logger` (e.g., `logger.error("message")`)
- Connection errors caught and logged but not raised (graceful degradation)
- `step()` in `call.py` is pure and never throws — all state transitions are safe
- Async errors: explicitly handled in background tasks with cleanup

**Example from `agent.py`:**
```python
async def _on_llm_token(self, token: str) -> None:
    """LLM produced a token — feed clean text to TTS."""
    if not self._active or not self._tts:
        return
    # ... processing
```

## Logging

**Framework:** `logging` module with custom `Logger` and `get_logger` wrapper in `log.py`

**Patterns:**
- Module-level logger: `logger = get_logger("shuo.module")`
- Log levels: `info()` for normal flow, `debug()` for verbose internal state, `error()` for failures
- Lifecycle events: `Logger.server_starting(port)`, `Logger.call_initiated(sid)`, etc.

## Function Design

**Pure functions:**
- `step(state, event) → (state, actions)` in `call.py` — the center of gravity
- All events and actions are frozen dataclasses
- Use `replace()` from dataclasses for state updates: `replace(state, phase=Phase.RESPONDING)`

**Async conventions:**
- Event queue for inter-task communication: `asyncio.Queue[Event]`
- Task creation for background work: `asyncio.create_task(_warmup())`
- Callbacks passed to services for results: `on_audio`, `on_done`
- `asyncio.call_soon(callback)` for fire-and-forget observers (non-blocking)

**Return Values:**
- Pure functions return tuples: `tuple[CallState, list[Action]]` in `step()`
- Events emitted via callbacks: `self._emit(AgentDoneEvent())`
- Async functions return None for fire-and-forget

## Module Design

**Flat package layout:**
- All core modules at `shuo/` root — no subdirectories
- No `__all__` declarations; all public names are importable
- Deferred imports inside function bodies to break circular chains

**Inactivity watchdog:**
- `_inactivity_watchdog(queue, timeout, last_activity)` in `call.py`
- `CALL_INACTIVITY_TIMEOUT = float(os.getenv("CALL_INACTIVITY_TIMEOUT", "300"))`
- Timeout configured via env var; watchdog cancels cleanly on `asyncio.CancelledError`

---

*Convention analysis updated: 2026-04-06*
