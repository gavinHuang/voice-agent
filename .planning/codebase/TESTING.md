# Testing Patterns

**Analysis Date:** 2026-03-18

## Test Framework

**Runner:**
- pytest 7.0.0+
- Config: No `pytest.ini` or `setup.cfg` found; uses pytest defaults
- Async support: `pytest-asyncio` 0.21.0+ for async test functions

**Run Commands:**
```bash
pytest                           # Run all tests
pytest -v                        # Verbose output
pytest tests/                    # Run specific directory
pytest path/to/test_file.py     # Run single test file
pytest -k "test_name"           # Run tests matching pattern
pytest --tb=short               # Shorter traceback format
```

**Test Files Location:**
- `shuo/tests/test_update.py` — State machine and event handling tests
- `ivr/tests/test_ivr.py` — IVR config parsing, TwiML rendering, and FastAPI endpoint tests
- Both test directories have `__init__.py` marker files

## Test File Organization

**Location:**
- `shuo/tests/` — Co-located with main package in parallel directory structure
- `ivr/tests/` — Same pattern for IVR module
- Not embedded in source tree; separate test directories at module level

**Naming:**
- `test_*.py` — Standard pytest convention
- Test classes: `Test*` prefix (e.g., `TestStreamLifecycle`, `TestFluxEndOfTurn`)
- Test methods: `test_*` lowercase with underscores (e.g., `test_stream_start_sets_stream_sid`)

**Structure:**
```
shuo/
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # (not found in shuo, but pattern used in ivr)
│   └── test_update.py           # State machine tests
ivr/
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # Fixtures for IVR tests
│   └── test_ivr.py              # Config, engine, and API tests
```

## Test Structure

**Suite Organization:**

From `shuo/tests/test_update.py`:
```python
class TestStreamLifecycle:
    """Group of related tests."""

    def test_stream_start_sets_stream_sid(self, initial_state):
        """Test description as docstring."""
        event = StreamStartEvent(stream_sid="new-stream-123")
        new_state, actions = process_event(initial_state, event)

        assert new_state.stream_sid == "new-stream-123"
        assert new_state.phase == Phase.LISTENING
        assert actions == []

class TestCompleteFlow:
    """Tests for end-to-end scenarios."""

    def test_full_conversation_turn(self, listening_state):
        """Multi-step conversation flow."""
        state = listening_state
        state, actions = process_event(state, FluxEndOfTurnEvent(transcript="Hello"))
        assert state.phase == Phase.RESPONDING
```

**Patterns:**
- Fixtures for reusable test state: `@pytest.fixture def initial_state() -> AppState`
- Test classes group related tests by behavior (lifecycle, routing, complete flows)
- Each test method is atomic and tests one behavior
- Assertions are explicit with clear expected values

## Fixtures and Factories

**Test Data (Fixtures):**

From `shuo/tests/test_update.py`:
```python
@pytest.fixture
def initial_state() -> AppState:
    """Fresh state at the start of a call."""
    return AppState()

@pytest.fixture
def listening_state() -> AppState:
    """State after stream has started."""
    return AppState(phase=Phase.LISTENING, stream_sid="test-stream-sid")

@pytest.fixture
def responding_state() -> AppState:
    """State while agent is responding."""
    return AppState(phase=Phase.RESPONDING, stream_sid="test-stream-sid")
```

From `ivr/tests/conftest.py`:
```python
SIMPLE_FLOW = """
name: Simple Test IVR
start: welcome

nodes:
  welcome:
    type: say
    say: "Welcome."
    next: main_menu
  main_menu:
    type: menu
    say: "Press 1 for option one..."
    routes:
      "1": option_one
      "2": option_two
"""

@pytest.fixture
def simple_flow() -> str:
    return SIMPLE_FLOW

@pytest.fixture
def client_simple(simple_flow):
    """AsyncClient with SIMPLE_FLOW loaded."""
    reload_config(simple_flow)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")
```

**Location:**
- State fixtures in test file headers
- IVR flow YAML strings defined in `conftest.py` as module-level strings
- Reusable clients (AsyncClient) created in fixtures with `ASGITransport`

## Mocking

**Framework:** No explicit mocking library imported; tests use real objects and pure functions

**Patterns:**
- Pure state machine: No mocking needed — `process_event(state, event)` is deterministic
- Async client testing: Uses `ASGITransport` with real FastAPI app (not mocked)
- Dataclass-based events: Can be constructed directly with test data

**What to Mock:**
- External HTTP services (not done yet, relies on test isolation via ASGITransport)
- Async I/O (not mocked; tests run with pytest-asyncio)

**What NOT to Mock:**
- Core state machine logic — test with real `AppState` dataclasses
- Event objects — instantiate directly
- FastAPI endpoints — use ASGITransport for full integration testing

## Coverage

**Requirements:** No coverage target enforced (no `pytest.ini` or config found)

**View Coverage:**
```bash
pytest --cov=shuo tests/              # Generate coverage report
pytest --cov=ivr --cov-report=html   # Generate HTML report
```

## Test Types

**Unit Tests:**
- Scope: Pure functions (state machine, event processing)
- Approach: Isolated, no external dependencies
- Example: `test_stream_start_sets_stream_sid()` — tests `process_event()` with single input
- File: `shuo/tests/test_update.py` (all tests here are unit tests of state machine)

**Integration Tests:**
- Scope: FastAPI endpoints with real app logic
- Approach: Use `ASGITransport` to call endpoints without HTTP
- Example: `test_step_say_node()` in `ivr/tests/test_ivr.py` — tests full path through FastAPI
- Fixtures: `client_simple`, `client_deep` provide pre-configured AsyncClient instances

**E2E Tests:**
- Scope: Real Twilio calls (optional)
- Framework: pytest with conditional skip
- Example: `test_e2e_real_call()` in `ivr/tests/test_ivr.py`
- Run: Requires `IVR_E2E=1` env var + real Twilio credentials
  ```bash
  IVR_E2E=1 TWILIO_ACCOUNT_SID=... TWILIO_AUTH_TOKEN=... pytest -k e2e
  ```

## Common Patterns

**Async Testing:**

From `ivr/tests/test_ivr.py`:
```python
@pytest.mark.anyio  # pytest-asyncio marker
async def test_health(client_simple):
    async with client_simple as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
```

**State Mutation Testing:**

From `shuo/tests/test_update.py`:
```python
def test_state_immutability(self, initial_state):
    """State updates should not mutate original."""
    event = StreamStartEvent(stream_sid="new-sid")
    new_state, _ = process_event(initial_state, event)

    assert initial_state.stream_sid is None      # Original unchanged
    assert new_state.stream_sid == "new-sid"     # New state updated
```

**Multi-Step Flow Testing:**

From `ivr/tests/test_ivr.py`:
```python
@pytest.mark.anyio
async def test_full_flow_option_one(client_simple):
    """Simulates a complete call: entry → welcome → menu → gather → result."""
    async with client_simple as c:
        r = await c.post("/twiml")                    # Entry point
        assert "welcome" in r.text

        r = await c.post("/ivr/step?node=welcome")    # Welcome node
        assert "Welcome" in r.text

        r = await c.post("/ivr/step?node=main_menu")  # Main menu
        assert "Gather" in r.text

        r = await c.post("/ivr/gather?node=main_menu", data={"Digits": "1"})
        assert "option_one" in r.text
```

**Error Handling & Edge Cases:**

From `shuo/tests/test_update.py`:
```python
class TestEdgeCases:

    def test_stream_stop_in_listening_is_safe(self, listening_state):
        """StreamStopEvent while listening should produce no actions."""
        _, actions = process_event(listening_state, StreamStopEvent())
        assert actions == []

    def test_agent_done_in_wrong_phase_is_safe(self, listening_state):
        """AgentTurnDoneEvent in LISTENING should not crash."""
        new_state, actions = process_event(listening_state, AgentTurnDoneEvent())
        assert new_state.phase == Phase.LISTENING
        assert actions == []
```

## Test Execution Patterns

**State Transition Assertions:**
```python
def test_end_of_turn_starts_agent(self, listening_state):
    event = FluxEndOfTurnEvent(transcript="Hello, how are you?")
    new_state, actions = process_event(listening_state, event)

    # State changes
    assert new_state.phase == Phase.RESPONDING

    # Actions generated
    assert len(actions) == 1
    assert isinstance(actions[0], StartAgentTurnAction)
    assert actions[0].transcript == "Hello, how are you?"
```

**Config Validation:**
```python
def test_config_rejects_unknown_start():
    with pytest.raises(ValueError, match="Start node"):
        parse_config({
            "name": "Bad",
            "start": "nonexistent",
            "nodes": {"a": {"type": "hangup"}},
        })
```

**XML Parsing (TwiML):**
```python
def test_render_say_node(simple_flow):
    engine = _engine(simple_flow)
    xml = engine.render_node("welcome")
    root = ET.fromstring(xml)

    say = root.find("Say")
    assert say is not None
    assert "Welcome" in say.text
```

---

*Testing analysis: 2026-03-18*
