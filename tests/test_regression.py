"""
Regression tests for bugs found and fixed during live testing.

REG-01: AMD hangup on "unknown" AnsweredBy — hangs up on live humans
REG-02: Goal not propagated when using standalone server — agent stays silent
REG-03: Initial greeting suppressed when goal is empty — agent stays silent
REG-04: IVR mode suppresses opening greeting — agent must listen first
"""

import os
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# =============================================================================
# REG-01: AMD AnsweredBy handling in /twiml endpoint
# =============================================================================

@pytest.fixture
def twiml_client(monkeypatch):
    """TestClient for /twiml with Twilio signature validation disabled."""
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("TWILIO_PUBLIC_URL", "https://example.ngrok.io")
    from fastapi.testclient import TestClient
    from shuo.web import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("answered_by", [
    "machine_start",
    "machine_end_beep",
    "machine_end_silence",
    "machine_end_other",
    "fax",
])
def test_amd_hangs_up_on_confirmed_machine(twiml_client, answered_by):
    """Confirmed machine/voicemail AnsweredBy values must produce a <Hangup/> response."""
    resp = twiml_client.post("/twiml", data={"AnsweredBy": answered_by})
    assert resp.status_code == 200
    assert "<Hangup" in resp.text, (
        f"Expected <Hangup/> for AnsweredBy={answered_by!r}, got: {resp.text[:200]}"
    )
    assert "<Connect" not in resp.text


@pytest.mark.parametrize("answered_by", [
    "human",
    "unknown",   # REG-01: was incorrectly hanging up on "unknown"
    "",          # no AMD parameter at all
])
def test_amd_connects_on_human_or_unknown(twiml_client, answered_by):
    """Human, unknown, or missing AnsweredBy must connect the WebSocket stream."""
    data = {"AnsweredBy": answered_by} if answered_by else {}
    resp = twiml_client.post("/twiml", data=data)
    assert resp.status_code == 200
    assert "<Hangup" not in resp.text, (
        f"Got unexpected <Hangup/> for AnsweredBy={answered_by!r}: {resp.text[:200]}"
    )
    assert "<Connect" in resp.text or "<Stream" in resp.text


def test_amd_unknown_was_previously_hanging_up():
    """
    Regression: the old condition `answered_by != 'human'` treated 'unknown' as machine.
    Verify the fix uses an explicit machine-values set instead.
    """
    import shuo.web as server_module
    import inspect
    src = inspect.getsource(server_module.twiml)
    # The fixed code must NOT contain the old catch-all condition
    assert 'answered_by != "human"' not in src, (
        "Old AMD condition `answered_by != 'human'` still present — REG-01 not fixed"
    )
    # Must use an explicit set of machine values
    assert "_machine_values" in src or "machine_start" in src, (
        "AMD fix must use an explicit set of machine values"
    )


# =============================================================================
# REG-02: Goal propagation — registry.set_pending / pop_pending roundtrip
# =============================================================================

def test_registry_set_and_pop_pending():
    """set_pending followed by pop_pending returns the correct goal and metadata."""
    from monitor import registry
    call_sid = "CA_reg_test_001"
    registry._pending.pop(call_sid, None)

    registry.set_pending(call_sid, phone="+61400000000", goal="Check today's date", ivr_mode=False)
    result = registry.pop_pending(call_sid)

    assert result["goal"] == "Check today's date"
    assert result["phone"] == "+61400000000"
    assert result["ivr_mode"] is False


def test_registry_pop_pending_missing_returns_defaults():
    """pop_pending on an unknown call_sid returns empty defaults (not KeyError)."""
    from monitor import registry
    result = registry.pop_pending("CA_nonexistent_sid")
    assert result["phone"] == ""
    assert result["goal"] == ""
    assert result["ivr_mode"] is False
    assert result.get("tenant_id", "default") == "default"


def test_registry_pop_pending_removes_entry():
    """After pop_pending, the entry is gone — second pop returns defaults."""
    from monitor import registry
    call_sid = "CA_reg_test_002"
    registry.set_pending(call_sid, phone="+1000000000", goal="test goal")
    registry.pop_pending(call_sid)
    second = registry.pop_pending(call_sid)
    assert second["goal"] == ""


def test_get_goal_falls_back_to_env_var(monkeypatch):
    """
    REG-02: When no pending entry exists (separate server process), get_goal falls
    back to CALL_GOAL env var so the agent still has a goal.
    """
    monkeypatch.setenv("CALL_GOAL", "Fallback goal from env")
    from monitor import registry
    call_sid = "CA_no_pending_sid"
    registry._pending.pop(call_sid, None)

    # Simulate what server.py's get_goal() does
    pending = registry.pop_pending(call_sid)
    goal = pending["goal"] or os.getenv("CALL_GOAL", "")
    assert goal == "Fallback goal from env"


def test_get_goal_prefers_registry_over_env_var(monkeypatch):
    """Registry goal takes precedence over CALL_GOAL env var."""
    monkeypatch.setenv("CALL_GOAL", "env goal")
    from monitor import registry
    call_sid = "CA_priority_test"
    registry.set_pending(call_sid, phone="+1000000000", goal="registry goal")

    pending = registry.pop_pending(call_sid)
    goal = pending["goal"] or os.getenv("CALL_GOAL", "")
    assert goal == "registry goal"


# =============================================================================
# REG-03 & REG-04: Initial greeting logic in run_conversation
# =============================================================================

def _make_fake_isp(goal="", ivr=False):
    """
    Return (mock_isp, mock_flux, mock_tts_pool, agent_turns) wired so that
    isp.start() fires stream-start then stream-stop, allowing run_conversation
    to complete in a test without real I/O.

    isp.start signature expected by run_conversation:
        start(on_media, on_start(stream_sid, call_sid, phone), on_stop)
    """
    mock_isp = AsyncMock()
    mock_isp.stop = AsyncMock()
    mock_isp.hangup = AsyncMock()

    agent_turns = []

    mock_agent = AsyncMock()
    mock_agent.start_turn = AsyncMock(side_effect=lambda msg, **kw: agent_turns.append(msg))
    mock_agent.cancel_turn = AsyncMock()
    mock_agent.restore_history = MagicMock(return_value=None)
    mock_agent.history = []

    mock_agent_cls = MagicMock(return_value=mock_agent)

    async def fake_isp_start(on_media, on_start, on_stop):
        # Fire stream-start with positional (stream_sid, call_sid, phone)
        await on_start("MZ_test", "CA_test", "+1000000000")
        await asyncio.sleep(0.05)   # let event loop process StreamStartEvent
        await on_stop()

    mock_isp.start = AsyncMock(side_effect=fake_isp_start)

    mock_flux = AsyncMock()
    mock_flux.start = AsyncMock()
    mock_flux.stop = AsyncMock()
    mock_flux.send = AsyncMock()

    mock_tts_pool = AsyncMock()
    mock_tts_pool.start = AsyncMock()

    return mock_isp, mock_flux, mock_tts_pool, mock_agent_cls, agent_turns


@pytest.mark.asyncio
async def test_greeting_sent_when_goal_set():
    """
    REG-03: When a goal is provided and not IVR mode, agent.start_turn is called
    with '[CALL_STARTED]' on StreamStart.
    """
    from shuo.call import run_call

    mock_isp, mock_flux, mock_tts_pool, mock_agent_cls, agent_turns = _make_fake_isp()

    with patch("shuo.speech.Transcriber", return_value=mock_flux), \
         patch("shuo.agent.Agent", mock_agent_cls):
        await run_call(
            mock_isp,
            get_goal=lambda call_sid: "Check today's date",
            voice_pool=mock_tts_pool,
        )

    assert len(agent_turns) >= 1, "agent.start_turn was never called — no opening greeting sent"
    assert agent_turns[0] == "[CALL_STARTED]", (
        f"Expected '[CALL_STARTED]' opener, got {agent_turns[0]!r}"
    )


@pytest.mark.asyncio
async def test_no_greeting_when_goal_empty():
    """
    REG-03: When goal is empty string, no opener is sent — agent stays silent
    (avoids unintelligible greetings when no task is defined).
    """
    from shuo.call import run_call

    mock_isp, mock_flux, mock_tts_pool, mock_agent_cls, agent_turns = _make_fake_isp()

    with patch("shuo.speech.Transcriber", return_value=mock_flux), \
         patch("shuo.agent.Agent", mock_agent_cls):
        await run_call(
            mock_isp,
            get_goal=lambda call_sid: "",  # empty goal
            voice_pool=mock_tts_pool,
        )

    assert agent_turns == [], (
        f"Expected no greeting when goal is empty, but agent.start_turn was called with: {agent_turns}"
    )


@pytest.mark.asyncio
async def test_no_greeting_in_ivr_mode():
    """
    REG-04: In IVR mode, agent must listen first — no opening greeting sent.
    The agent responds only after the IVR's EndOfTurn fires.
    """
    from shuo.call import run_call

    mock_isp, mock_flux, mock_tts_pool, mock_agent_cls, agent_turns = _make_fake_isp(ivr=True)

    with patch("shuo.speech.Transcriber", return_value=mock_flux), \
         patch("shuo.agent.Agent", mock_agent_cls):
        await run_call(
            mock_isp,
            get_goal=lambda call_sid: "Navigate the IVR menu",
            ivr_mode=lambda: True,   # IVR mode on
            voice_pool=mock_tts_pool,
        )

    assert agent_turns == [], (
        f"IVR mode: expected no greeting on StreamStart, but got: {agent_turns}"
    )


@pytest.mark.asyncio
async def test_custom_initial_message_overrides_call_started(monkeypatch):
    """INITIAL_MESSAGE env var is used as opener instead of '[CALL_STARTED]'."""
    monkeypatch.setenv("INITIAL_MESSAGE", "Hi there, calling to confirm your appointment.")

    from shuo.call import run_call

    mock_isp, mock_flux, mock_tts_pool, mock_agent_cls, agent_turns = _make_fake_isp()

    with patch("shuo.speech.Transcriber", return_value=mock_flux), \
         patch("shuo.agent.Agent", mock_agent_cls):
        await run_call(
            mock_isp,
            get_goal=lambda call_sid: "Confirm appointment",
            voice_pool=mock_tts_pool,
        )

    assert len(agent_turns) >= 1
    assert agent_turns[0] == "Hi there, calling to confirm your appointment.", (
        f"Expected INITIAL_MESSAGE as opener, got {agent_turns[0]!r}"
    )


# =============================================================================
# REG-02 (integration): Dashboard /call endpoint registers goal in-process
# =============================================================================

@pytest.fixture
def dashboard_client(monkeypatch):
    """TestClient for the full app with dashboard router mounted."""
    monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
    monkeypatch.setenv("TWILIO_PUBLIC_URL", "https://example.ngrok.io")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test_token")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15550000000")
    from fastapi.testclient import TestClient
    from shuo.web import app
    return TestClient(app, raise_server_exceptions=False)


def test_dashboard_call_registers_pending_goal(monkeypatch, dashboard_client):
    """
    REG-02: POST /dashboard/call must register goal via registry.set_pending
    so the running server's get_goal() can retrieve it when the call connects.
    """
    from monitor import registry

    fake_call_sid = "CA_dashboard_test_001"

    with patch("shuo.phone.dial_out", return_value=fake_call_sid):
        resp = dashboard_client.post(
            "/dashboard/call",
            json={"phone": "+61400000001", "goal": "Check today's date", "ivr_mode": False},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "calling"
    assert data["call_sid"] == fake_call_sid

    # Goal must be registered in the registry for the running server to find
    pending = registry.pop_pending(fake_call_sid)
    assert pending["goal"] == "Check today's date", (
        f"Goal not registered in registry — running server won't speak. Got: {pending!r}"
    )
    assert pending["phone"] == "+61400000001"
    assert pending["ivr_mode"] is False


def test_dashboard_call_ivr_mode_sets_flag(monkeypatch, dashboard_client):
    """ivr_mode=True from dashboard must be stored in pending so the agent listens first."""
    from monitor import registry

    fake_call_sid = "CA_ivr_mode_test"

    with patch("shuo.phone.dial_out", return_value=fake_call_sid):
        resp = dashboard_client.post(
            "/dashboard/call",
            json={"phone": "+61400000002", "goal": "Navigate IVR menu", "ivr_mode": True},
        )

    assert resp.status_code == 200
    pending = registry.pop_pending(fake_call_sid)
    assert pending["ivr_mode"] is True


# =============================================================================
# POST /call — goal-directed outbound call API
# =============================================================================

@pytest.fixture
def call_api_client(monkeypatch):
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("TWILIO_PUBLIC_URL", "https://example.ngrok.io")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "toktest")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15550000000")
    from fastapi.testclient import TestClient
    from shuo.web import app
    return TestClient(app, raise_server_exceptions=False)


def test_post_call_endpoint_returns_call_info(monkeypatch, call_api_client):
    """POST /call/{phone} with full CallContext body returns call_sid and call_id."""
    from monitor import registry

    fake_call_sid = "CA_post_test_001"

    with patch("shuo.web.dial_out", return_value=fake_call_sid):
        resp = call_api_client.post(
            "/call/+61400000099",
            json={"goal": "Book a dental appointment", "agent_name": "Sam"},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["call_sid"] == fake_call_sid
    assert "call_id" in data
    assert data["goal"] == "Book a dental appointment"

    # Goal must be registered in pending for the running server to find
    pending = registry.pop_pending(fake_call_sid)
    assert pending["goal"] == "Book a dental appointment"


def test_post_call_endpoint_missing_goal_returns_422(call_api_client):
    """POST /call without goal returns HTTP 422 validation error."""
    resp = call_api_client.post(
        "/call/+61400000099",
        json={"agent_name": "Sam"},  # no goal
    )
    assert resp.status_code == 422


def test_post_call_endpoint_unknown_tenant_returns_404(call_api_client):
    """POST /call with unknown tenant_id returns HTTP 404."""
    resp = call_api_client.post(
        "/call/+61400000099",
        json={"goal": "Test", "tenant_id": "no-such-tenant"},
    )
    assert resp.status_code == 404
    assert "tenant not found" in resp.json().get("error", "")


def test_get_call_endpoint_still_works(call_api_client):
    """Legacy GET /call?goal= endpoint still works after adding POST variant."""
    fake_call_sid = "CA_get_test_001"

    with patch("shuo.web.dial_out", return_value=fake_call_sid):
        resp = call_api_client.get("/call/+61400000099?goal=Check+status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "calling"
    assert data["goal"] == "Check status"
