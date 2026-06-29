"""
Tests for per-user usage limits (issue #1: "Set limit for each user").

- Dashboard auth also accepts INTERNAL_API_KEY (proxy shared secret).
- POST /call is blocked with 403 once a tenant exceeds their total-minutes limit.
- Under the limit, the call proceeds and a per-call duration watchdog is scheduled.
- resolve_user_limits() applies per-user overrides and falls back to env defaults.
- used_minutes() sums call_logs.duration_s and converts to minutes.
"""
import asyncio
from unittest.mock import patch, AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app():
    app = FastAPI()
    from monitor.server import router
    app.include_router(router)
    return app


def _call_client(monkeypatch):
    """TestClient with dashboard auth disabled and a high rate limit."""
    monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
    monkeypatch.setenv("CALL_RATE_LIMIT", "100")
    return TestClient(_make_app(), raise_server_exceptions=False)


# ── Auth: INTERNAL_API_KEY accepted on dashboard routes ───────────────────────

def test_dashboard_accepts_internal_api_key(monkeypatch):
    monkeypatch.setenv("DASHBOARD_API_KEY", "dash-key")
    monkeypatch.setenv("INTERNAL_API_KEY", "internal-key")
    client = TestClient(_make_app(), raise_server_exceptions=False)

    assert client.get("/dashboard/calls", headers={"X-API-Key": "internal-key"}).status_code == 200
    assert client.get("/dashboard/calls", headers={"X-API-Key": "dash-key"}).status_code == 200
    assert client.get("/dashboard/calls", headers={"X-API-Key": "nope"}).status_code == 401


# ── Total-minutes limit enforcement ───────────────────────────────────────────

def test_total_minutes_limit_blocks_call(monkeypatch):
    client = _call_client(monkeypatch)
    payload = {"phone": "+15550001111", "goal": "x", "tenant_id": "t1"}
    with patch("monitor.server.resolve_user_limits", new=AsyncMock(return_value=(60.0, 10.0))), \
         patch("monitor.server.used_minutes", new=AsyncMock(return_value=75.0)), \
         patch("shuo.phone.dial_out", return_value="CA1"), \
         patch("monitor.registry.set_pending"):
        resp = client.post("/dashboard/call", json=payload)
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"].startswith("Usage limit reached")
    assert body["used_minutes"] == 75.0
    assert body["limit_minutes"] == 60.0


def test_under_limit_allows_and_schedules_watchdog(monkeypatch):
    client = _call_client(monkeypatch)
    payload = {"phone": "+15550001111", "goal": "x", "tenant_id": "t1"}
    with patch("monitor.server.resolve_user_limits", new=AsyncMock(return_value=(60.0, 10.0))), \
         patch("monitor.server.used_minutes", new=AsyncMock(return_value=5.0)), \
         patch("monitor.server._schedule_call_duration_limit") as sched, \
         patch("shuo.phone.dial_out", return_value="CA1"), \
         patch("monitor.registry.set_pending"):
        resp = client.post("/dashboard/call", json=payload)
    assert resp.status_code == 200
    sched.assert_called_once_with("CA1", 10.0)


def test_supabase_failure_does_not_block_call(monkeypatch):
    """A Supabase lookup error must never hard-block a call."""
    client = _call_client(monkeypatch)
    payload = {"phone": "+15550001111", "goal": "x", "tenant_id": "t1"}
    with patch("monitor.server.resolve_user_limits", new=AsyncMock(return_value=(60.0, 10.0))), \
         patch("monitor.server.used_minutes", new=AsyncMock(side_effect=RuntimeError("supabase down"))), \
         patch("shuo.phone.dial_out", return_value="CA1"), \
         patch("monitor.registry.set_pending"):
        resp = client.post("/dashboard/call", json=payload)
    assert resp.status_code == 200


# ── Limit resolution helpers ──────────────────────────────────────────────────

def test_resolve_user_limits_override_and_fallback():
    import monitor.server as srv
    rows = [{"total_minutes_limit": 120, "per_call_minutes_limit": None}]
    with patch("monitor.server._supabase_get", new=AsyncMock(return_value=rows)):
        total, per_call = asyncio.run(srv.resolve_user_limits("t1"))
    assert total == 120.0                       # per-user override
    assert per_call == srv.PER_CALL_MINUTES_LIMIT  # NULL falls back to env default


def test_resolve_user_limits_defaults_when_no_row():
    import monitor.server as srv
    with patch("monitor.server._supabase_get", new=AsyncMock(return_value=[])):
        total, per_call = asyncio.run(srv.resolve_user_limits("unknown"))
    assert total == srv.USER_TOTAL_MINUTES_LIMIT
    assert per_call == srv.PER_CALL_MINUTES_LIMIT


def test_used_minutes_sums_durations():
    import monitor.server as srv
    rows = [{"duration_s": 120.0}, {"duration_s": 60.0}, {"duration_s": None}]
    with patch("monitor.server._supabase_get", new=AsyncMock(return_value=rows)):
        used = asyncio.run(srv.used_minutes("t1"))
    assert used == 3.0  # (120 + 60 + 0) / 60
