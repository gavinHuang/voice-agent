# Coding Conventions

**Analysis Date:** 2026-03-18

## Naming Patterns

**Files:**
- Module names: lowercase with underscores (e.g., `llm.py`, `tts_pool.py`, `twilio_client.py`)
- Test files: `test_*.py` prefix (e.g., `test_update.py`, `test_ivr.py`)
- Service files: descriptive names in `services/` directory (e.g., `tts_kokoro.py`, `flux_pool.py`)

**Functions:**
- snake_case for all functions and methods (e.g., `start_turn()`, `process_event()`, `on_flux_end_of_turn()`)
- Private methods: leading underscore prefix (e.g., `_on_llm_token()`, `_handle_sigterm()`, `_is_dtmf()`)
- Callback functions: `on_*` prefix for event handlers (e.g., `on_token()`, `on_done()`, `on_agent_ready()`)
- Async functions: no special prefix, just `async def` (e.g., `async def start_turn()`)

**Variables:**
- snake_case for all variables and attributes (e.g., `stream_sid`, `active_calls`, `tts_pool`)
- Private attributes: leading underscore (e.g., `self._active`, `self._llm`, `self._tts_pool`)
- Boolean flags: prefixed with `is_` or descriptive state name (e.g., `is_active`, `is_playing`, `_got_first_token`)
- Protected module-level state: underscore prefix (e.g., `_active_calls`, `_draining`, `_dtmf_pending`)

**Types & Classes:**
- PascalCase for classes (e.g., `Agent`, `AppState`, `MarkerScanner`, `TTSPool`)
- Enum members: UPPER_CASE (e.g., `Phase.LISTENING`, `Phase.RESPONDING`)
- Type unions: declared with `Union[Type1, Type2]` or using dataclass Union aliases (e.g., `Event = Union[StreamStartEvent, ...]`)
- Dataclasses: frozen immutable types used for events and state (e.g., `@dataclass(frozen=True) class AppState`)

## Code Style

**Formatting:**
- No explicit formatter configured (no `.prettierrc`, `black.toml`, or `ruff.toml` found)
- Indentation: 4 spaces (Python standard)
- Line length: implicit, but code typically uses 100-120 character lines
- String style: both single and double quotes used; no enforced convention observed

**Linting:**
- No linting config found (no `.pylintrc`, `.flake8`, or `pyproject.toml`)
- Code follows PEP 8 conventions implicitly

**Docstring Style:**
- Module-level docstrings: triple quotes with description and usage examples
  ```python
  """
  Agent -- self-contained LLM -> TTS -> Player pipeline.

  Encapsulates the entire agent response lifecycle.
  """
  ```
- Function docstrings: concise one-liners for simple functions, multi-line for complex
  ```python
  def feed(self, token: str) -> tuple[str, list[str]]:
      """Process one token. Returns (clean_text, list_of_markers)."""
  ```
- Class docstrings: Describe purpose and ownership
  ```python
  class Agent:
      """
      Self-contained agent response pipeline.

      LLM is persistent (keeps conversation history across turns).
      """
  ```

## Import Organization

**Order:**
1. Standard library imports (`asyncio`, `json`, `os`, `sys`, `time`, `logging`)
2. Third-party imports (`fastapi`, `websockets`, `twilio`, `openai`, `numpy`)
3. Relative local imports (from `.agent`, `from .services.llm`)

**Path Aliases:**
- No path aliases configured; uses relative imports within package
- Common pattern: `from .services.tts_pool import TTSPool` (relative parent package reference)
- Absolute imports from dashboard: `from dashboard.server import router as dashboard_router`

**Example from `shuo/server.py`:**
```python
import json
import os
import asyncio
from typing import List, Optional

from fastapi import FastAPI, WebSocket
from openai import AsyncOpenAI

from .conversation import run_conversation_over_twilio
from .services.tts_pool import TTSPool
```

## Error Handling

**Patterns:**
- Try/except with specific error logging via `ServiceLogger` (e.g., `log.error("message", exc)`)
- Connection errors caught and logged but not raised (graceful degradation)
- State machine uses immutable dataclasses with validation at parse time
- `process_event()` in `state.py` is pure and never throws — all state transitions are safe
- Async errors: explicitly handled in background tasks with cleanup (see `conversation.py`)

**Example from `agent.py`:**
```python
async def _on_llm_token(self, token: str) -> None:
    """LLM produced a token -> scan for markers, feed clean text to TTS."""
    if not self._active or not self._tts:
        return
    # ... processing
```

**Example from `conversation.py`:**
```python
async def read_twilio() -> None:
    """Background task to read from Twilio and push to event queue."""
    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            event = parse_twilio_message(data)
            if event:
                await event_queue.put(event)
    except Exception as e:
        logger.error(f"Error reading from Twilio: {e}")
```

## Logging

**Framework:** `logging` module with custom `ServiceLogger` wrapper

**Patterns:**
- Module-level logger: `logger = get_logger("shuo.module")` or `log = ServiceLogger("ServiceName")`
- Service loggers have color coding: Flux (blue), LLM (magenta), TTS (cyan), Agent (green)
- Log levels: `info()` for normal flow, `debug()` for verbose internal state, `error()` for failures
- Structured logging: Prefix patterns like "⏱ LLM first token", "✓ Connected", "✗ Error"

**Example from `agent.py`:**
```python
log = ServiceLogger("Agent")
log.info(f"Turn started  (TTS {tts_ms}ms = {tts_ms}ms setup)")
log.info(f"⏱  LLM first token  +{_ms_since(self._t0)}ms")
```

**Example from `log.py` (lifecycle logging):**
```python
@classmethod
def server_starting(cls, port: int) -> None:
    cls._logger.info("\U0001F680 " + _c(C.CYAN, "Server starting on port " + str(port)))

def event(self, event: Event) -> None:
    """Log an incoming event."""
```

## Comments

**When to Comment:**
- Algorithm explanation: Comments above non-obvious logic (e.g., marker scanning in `MarkerScanner`)
- State machine transitions: Comments explaining phase changes and event handling
- Configuration and constants: Why a value was chosen (e.g., `MAX_BUF = 20  # Max chars before timeout`)
- TODO/FIXME: Direct problem statement, not solutions
  - Pattern: `# TODO: [Issue description]` or `# FIXME: [Problem statement]`

**JSDoc/Type Hints:**
- Uses Python 3.10+ type hints: `def feed(self, token: str) -> tuple[str, list[str]]`
- Optional types: `Optional[str]`, `Optional[Callable[[str], Awaitable[None]]]`
- Union types: `Union[StreamStartEvent, StreamStopEvent, MediaEvent]`
- Callback types fully annotated: `Callable[[str], Awaitable[None]]` for async callbacks
- No docstring-based type hints; relies on annotations

## Function Design

**Size:**
- Typical functions: 20-40 lines
- Complex pipelines: decomposed into smaller async tasks (e.g., `read_twilio()`, `read_deepgram()`)
- Callback handlers: 5-15 lines, focused on event routing

**Parameters:**
- Positional for essential parameters (state, event)
- Keyword for optional callbacks: `on_token: Optional[Callable[[str], Awaitable[None]]] = None`
- Dataclass parameters used for immutable state passing (e.g., `AppState`, events)
- Event objects as singular parameter instead of multiple fields

**Return Values:**
- Pure functions return tuples: `Tuple[AppState, List[Action]]` in `state.py`
- Async functions return awaitable results or None for fire-and-forget
- Events emitted via callbacks rather than return values: `self._emit(AgentTurnDoneEvent())`

**Example from `state.py` (pure function):**
```python
def process_event(state: AppState, event: Event) -> Tuple[AppState, List[Action]]:
    """Pure state machine: (State, Event) -> (State, Actions)"""
    if isinstance(event, StreamStartEvent):
        return replace(state, stream_sid=event.stream_sid, phase=Phase.LISTENING), []
```

## Module Design

**Exports:**
- Implicit: All public classes and functions are importable
- Pattern: `from .agent import Agent` (public class) vs `from .log import ServiceLogger` (utility)
- No `__all__` declarations observed

**Barrel Files:**
- `services/__init__.py` is mostly empty; imports are explicit: `from .services.llm import LLMService`
- Main package `__init__.py` minimal; entry point is `main.py` in root or `server.py` in subpackage

**IVR Module Organization:**
- `ivr/config.py`: Configuration parsing
- `ivr/engine.py`: TwiML rendering
- `ivr/server.py`: FastAPI app
- Clear separation of concerns: config → logic → HTTP endpoints

## Async/Await Patterns

**Conventions:**
- Async context managers for resource cleanup: `async with client_simple as c:`
- Task creation for background work: `asyncio.create_task(_warmup())`
- Event queue for inter-task communication: `event_queue: asyncio.Queue[Event]`
- Callback-based event emission for agent turns (events not awaited on dispatch)

**Example from `conversation.py`:**
```python
async def run_conversation_over_twilio(...) -> None:
    event_queue: asyncio.Queue[Event] = asyncio.Queue()

    async def on_flux_end_of_turn(transcript: str) -> None:
        await event_queue.put(FluxEndOfTurnEvent(transcript=transcript))
```

## Type System

**Dataclasses for Immutable State:**
- All events are frozen dataclasses (enforce immutability and hashability)
- `AppState` is frozen to prevent accidental mutations
- Use `replace()` from dataclasses for state updates: `replace(state, phase=Phase.LISTENING)`

**Union Types for Events & Actions:**
- Single `Event` and `Action` union types prevent cascading isinstance checks
- Exhaustive pattern matching via isinstance in event handlers

**Example from `types.py`:**
```python
@dataclass(frozen=True)
class AppState:
    """Application state -- just routing information."""
    phase: Phase = Phase.LISTENING
    stream_sid: Optional[str] = None
    hold_mode: bool = False

Event = Union[
    StreamStartEvent, StreamStopEvent, MediaEvent,
    FluxStartOfTurnEvent, FluxEndOfTurnEvent,
    ...
]
```

---

*Convention analysis: 2026-03-18*
