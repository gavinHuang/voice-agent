# Testing Patterns

**Analysis Date:** 2026-04-06 (updated after greenfield module refactor)

## Test Framework

**Runner:**
- pytest 9.0+
- Config: `pyproject.toml` (asyncio mode: strict)
- Async support: `pytest-asyncio` for async test functions

**Run Commands:**
```bash
python -m pytest tests/ -v              # Core tests (133 tests, ~0.03s)
python -m pytest simulator/tests/ -v   # IVR integration tests (~1s)
python -m pytest tests/test_update.py  # Run single test file
python -m pytest -k "test_name"        # Run tests matching pattern
python -m pytest --tb=short            # Shorter traceback format
```

**Test status:** 133/133 pass. 6 known benign warnings (websockets.legacy deprecation, FastAPI on_event deprecation — do not fix).

## Test File Organization

**Location:**
- `tests/` — Core package tests (state machine, agent, CLI, security, benchmarks)
- `simulator/tests/` — IVR simulator integration tests (no network access)

**Test files:**
```
tests/
├── __init__.py
├── conftest.py              # Adds project root to sys.path
├── test_update.py           # State machine: step(), CallState, events, actions
├── test_agent.py            # Agent pipeline: LanguageModel, tool calls
├── test_bench.py            # Benchmark: scenario loading, IVRDriver, run_scenario
├── test_bug_fixes.py        # Race conditions: _dtmf_lock, VoicePool lock, watchdog
├── test_cli.py              # CLI commands: serve, call, local-call, bench, config
├── test_dashboard_auth.py   # Auth + rate limiting on /dashboard/* routes
├── test_isp.py              # Phone abstraction: TwilioPhone, LocalPhone
├── test_ivr_barge_in.py     # IVR mode barge-in suppression
├── test_regression.py       # E2E: AMD, registry, goal routing, dashboard call
└── test_webhook_security.py # Twilio signature validation + trace rotation
simulator/tests/
├── __init__.py
├── conftest.py              # Fixtures for IVR tests (YAML flow strings, AsyncClient)
└── test_ivr.py              # Config parsing, TwiML rendering, routing, full flow
```

## Test Structure

**Suite Organization:**

From `tests/test_update.py`:
```python
class TestStreamLifecycle:
    def test_stream_start_transitions_to_listening(self):
        event = CallStartedEvent()
        new_state, actions = step(CallState(), event)
        assert new_state.phase == Phase.LISTENING

class TestUserSpokeEvent:
    def test_end_of_turn_starts_agent(self, listening_state):
        event = UserSpokeEvent(transcript="Hello")
        new_state, actions = step(listening_state, event)
        assert new_state.phase == Phase.RESPONDING
        assert isinstance(actions[0], StartTurnAction)
```

**Patterns:**
- Fixtures for reusable test state: `@pytest.fixture def listening_state() -> CallState`
- Test classes group related tests by behavior (lifecycle, routing, complete flows)
- Each test method is atomic and tests one behavior
- `@pytest.mark.asyncio` for async tests (strict mode)

## Fixtures and Factories

**State fixtures (tests/test_update.py):**
```python
@pytest.fixture
def listening_state() -> CallState:
    return CallState(phase=Phase.LISTENING)

@pytest.fixture
def responding_state() -> CallState:
    return CallState(phase=Phase.RESPONDING)
```

**IVR client fixtures (simulator/tests/conftest.py):**
```python
SIMPLE_FLOW = """
name: Simple Test IVR
start: welcome
nodes:
  welcome:
    type: say
    say: "Welcome."
    next: main_menu
  ...
"""

@pytest.fixture
def client_simple(simple_flow):
    reload_config(simple_flow)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")
```

## Mocking

**Framework:** `unittest.mock` (patch, AsyncMock, MagicMock)

**Patterns:**
- Pure state machine: No mocking needed — `step(state, event)` is deterministic
- Async client testing: Uses `ASGITransport` with real FastAPI app
- Service mocking: `patch("shuo.agent.Agent")`, `patch("shuo.speech.Transcriber")`
- Phone mocking: `MockPhone` class implementing the `Phone` protocol

**Patch locations (important):**
- Patch where the name is imported, not where it's defined:
  - `patch("shuo.speech.Transcriber")` — not `shuo.call.Transcriber` (it's deferred-imported)
  - `patch("shuo.agent.Agent")` — same reason
  - `patch("shuo.phone.dial_out")` — not `shuo.cli.dial_out` (imported inside function body)
  - `patch("shuo.call.run_call")` — for CLI/bench tests that import it deferred
- Monitor module: `patch("monitor.registry.all_calls")`, `patch("monitor.server._call_limiter")`

## Test Types

**Unit Tests:**
- Scope: Pure functions (state machine, criteria evaluation)
- Approach: Isolated, no external dependencies
- Example: `test_end_of_turn_starts_agent()` — tests `step()` with single input
- Files: `tests/test_update.py`, `tests/test_bench.py` (criteria evaluation)

**Integration Tests:**
- Scope: FastAPI endpoints with real app logic
- Approach: Use `ASGITransport` or `TestClient` to call endpoints without HTTP
- Example: `test_twilio_post_twiml_valid_signature()` in `test_webhook_security.py`
- Files: `tests/test_regression.py`, `tests/test_dashboard_auth.py`, `simulator/tests/test_ivr.py`

**E2E Tests:**
- Scope: Full call flow simulation (agent + IVR mock server)
- Example: `test_sample_scenarios_pass` in `tests/test_bench.py` — spawns real IVR server
- Requires: No external network, uses LocalPhone + IVR mock

**Async/Concurrency Tests:**
- `test_dtmf_lock_concurrent` — 50 concurrent writers to `_dtmf_pending`
- `test_tts_pool_concurrent_evict` — interleaved `get()` and `_evict_stale()`
- `test_token_observer_nonblocking` — slow observer must not delay `_on_llm_token`
- `test_inactivity_watchdog_fires` — watchdog fires `HangupEvent` after timeout

## Common Patterns

**State Machine Testing:**
```python
def test_end_of_turn_starts_agent(self, listening_state):
    event = UserSpokeEvent(transcript="Hello, how are you?")
    new_state, actions = step(listening_state, event)

    assert new_state.phase == Phase.RESPONDING
    assert len(actions) == 1
    assert isinstance(actions[0], StartTurnAction)
    assert actions[0].transcript == "Hello, how are you?"
```

**Async Endpoint Testing:**
```python
@pytest.mark.asyncio
async def test_full_flow_option_one(client_simple):
    async with client_simple as c:
        r = await c.post("/twiml")
        assert "welcome" in r.text

        r = await c.post("/ivr/step?node=welcome")
        assert "Welcome" in r.text

        r = await c.post("/ivr/gather?node=main_menu", data={"Digits": "1"})
        assert "option_one" in r.text
```

**Monitoring/patching run_call:**
```python
@pytest.mark.asyncio
async def test_greeting_sent_when_goal_set(monkeypatch):
    with patch("shuo.speech.Transcriber", return_value=mock_flux), \
         patch("shuo.agent.Agent", mock_agent_cls):
        await run_call(mock_phone, get_goal=lambda: "Book a table",
                       voice_pool=mock_tts_pool)
```

**CLI testing (fake module injection):**
```python
# Inject fake shuo.web into sys.modules to avoid dashboard ImportError
sys.modules["shuo.web"] = fake_server_module
with patch("shuo.phone.dial_out", fake_make_call):
    result = runner.invoke(cli, ["call", "+1234567890"])
```

---

*Testing analysis updated: 2026-04-06*
