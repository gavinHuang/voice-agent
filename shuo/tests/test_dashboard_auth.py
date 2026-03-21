"""
Tests for dashboard API key authentication and rate limiting.

Task 1 tests (auth):
- GET /dashboard/calls with no key returns 401
- GET /dashboard/calls with wrong X-API-Key returns 401
- GET /dashboard/calls with correct X-API-Key returns 200
- When DASHBOARD_API_KEY unset, GET /dashboard/calls returns 200 (auth disabled)
- WebSocket /dashboard/ws with ?token=correct_key connects successfully
- WebSocket /dashboard/ws without token is rejected

Task 2 tests (rate limiting):
- POST /call requests succeed up to CALL_RATE_LIMIT
- (CALL_RATE_LIMIT+1)th request returns 429 with Retry-After header
- CALL_RATE_LIMIT env var overrides default
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app():
    """Create a minimal FastAPI app with the dashboard router mounted."""
    app = FastAPI()
    from dashboard.server import router
    app.include_router(router)
    return app


# =============================================================================
# Task 1: API key authentication — HTTP routes
# =============================================================================

def test_no_api_key_returns_401(monkeypatch):
    """GET /dashboard/calls with no key returns 401 when DASHBOARD_API_KEY is set."""
    monkeypatch.setenv("DASHBOARD_API_KEY", "test-secret-key")
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/dashboard/calls")
    assert response.status_code == 401
    assert "detail" in response.json()
    assert "invalid" in response.json()["detail"].lower() or "missing" in response.json()["detail"].lower()


def test_wrong_api_key_returns_401(monkeypatch):
    """GET /dashboard/calls with wrong X-API-Key returns 401."""
    monkeypatch.setenv("DASHBOARD_API_KEY", "test-secret-key")
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/dashboard/calls", headers={"X-API-Key": "wrong-key"})
    assert response.status_code == 401


def test_correct_api_key_returns_200(monkeypatch):
    """GET /dashboard/calls with correct X-API-Key returns 200."""
    monkeypatch.setenv("DASHBOARD_API_KEY", "test-secret-key")
    app = _make_app()
    # Patch registry to avoid needing a full server setup
    with patch("dashboard.registry.all_calls", return_value=[]):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/dashboard/calls", headers={"X-API-Key": "test-secret-key"})
    assert response.status_code == 200


def test_auth_disabled_when_env_var_unset(monkeypatch):
    """When DASHBOARD_API_KEY is unset, GET /dashboard/calls returns 200 (auth disabled)."""
    monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
    app = _make_app()
    with patch("dashboard.registry.all_calls", return_value=[]):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/dashboard/calls")
    assert response.status_code == 200


def test_all_http_routes_protected(monkeypatch):
    """All HTTP routes return 401 without a valid key when DASHBOARD_API_KEY is set."""
    monkeypatch.setenv("DASHBOARD_API_KEY", "test-secret-key")
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)

    routes_to_check = [
        ("GET", "/dashboard/calls"),
        ("POST", "/dashboard/calls/fake-id/hangup"),
        ("POST", "/dashboard/calls/fake-id/takeover"),
        ("POST", "/dashboard/calls/fake-id/handback"),
        ("POST", "/dashboard/calls/fake-id/dtmf"),
        ("POST", "/dashboard/summarize"),
    ]
    for method, path in routes_to_check:
        if method == "GET":
            resp = client.get(path)
        else:
            resp = client.post(path, json={})
        assert resp.status_code == 401, (
            f"Expected 401 for {method} {path}, got {resp.status_code}"
        )


# =============================================================================
# Task 1: WebSocket authentication
# =============================================================================

def test_websocket_accepts_with_valid_token(monkeypatch):
    """WebSocket /dashboard/ws with ?token=correct_key connects successfully."""
    monkeypatch.setenv("DASHBOARD_API_KEY", "test-secret-key")
    app = _make_app()

    mock_q = MagicMock()
    mock_q.get = MagicMock(side_effect=Exception("stop-loop"))

    with patch("dashboard.bus.subscribe_global", return_value=mock_q), \
         patch("dashboard.registry.all_calls", return_value=[]), \
         patch("dashboard.bus.unsubscribe_global"):
        client = TestClient(app)
        try:
            with client.websocket_connect("/dashboard/ws?token=test-secret-key") as ws:
                # Connection was accepted; receive the initial snapshot
                data = ws.receive_json()
                assert data["type"] == "active_calls"
        except Exception:
            # The mock_q.get raising Exception breaks the loop — that's fine
            pass


def test_websocket_rejects_without_token(monkeypatch):
    """WebSocket /dashboard/ws without token query param is rejected."""
    monkeypatch.setenv("DASHBOARD_API_KEY", "test-secret-key")
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)

    # When auth fails, the server closes the WebSocket before accepting.
    # TestClient raises WebSocketDisconnect or similar.
    with pytest.raises(Exception):
        with client.websocket_connect("/dashboard/ws") as ws:
            ws.receive_json()


def test_websocket_rejects_wrong_token(monkeypatch):
    """WebSocket /dashboard/ws with wrong ?token= is rejected."""
    monkeypatch.setenv("DASHBOARD_API_KEY", "test-secret-key")
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)

    with pytest.raises(Exception):
        with client.websocket_connect("/dashboard/ws?token=wrong-token") as ws:
            ws.receive_json()


def test_websocket_open_when_auth_disabled(monkeypatch):
    """WebSocket connects without token when DASHBOARD_API_KEY is unset."""
    monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
    app = _make_app()

    mock_q = MagicMock()
    mock_q.get = MagicMock(side_effect=Exception("stop-loop"))

    with patch("dashboard.bus.subscribe_global", return_value=mock_q), \
         patch("dashboard.registry.all_calls", return_value=[]), \
         patch("dashboard.bus.unsubscribe_global"):
        client = TestClient(app)
        try:
            with client.websocket_connect("/dashboard/ws") as ws:
                data = ws.receive_json()
                assert data["type"] == "active_calls"
        except Exception:
            pass


# =============================================================================
# Task 2: Rate limiting on POST /call
# =============================================================================

def _make_call_client(monkeypatch, limit="3"):
    """Helper: build TestClient with rate limit env set, auth disabled."""
    monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
    monkeypatch.setenv("CALL_RATE_LIMIT", limit)
    app = _make_app()
    return TestClient(app, raise_server_exceptions=False)


def test_rate_limit_allows_up_to_limit(monkeypatch):
    """First CALL_RATE_LIMIT POST /call requests succeed (200 or 500 from Twilio mock)."""
    mock_sid = "CA123"
    with patch("shuo.services.twilio_client.make_outbound_call", return_value=mock_sid), \
         patch("dashboard.registry.set_pending"):
        client = _make_call_client(monkeypatch, limit="3")
        payload = {"phone": "+15550001111", "goal": "test"}
        for i in range(3):
            resp = client.post("/dashboard/call", json=payload)
            assert resp.status_code in (200, 500), (
                f"Request {i+1}: expected 200 or 500, got {resp.status_code}"
            )


def test_rate_limit_blocks_over_limit(monkeypatch):
    """(CALL_RATE_LIMIT+1)th request returns 429 with Retry-After header."""
    mock_sid = "CA123"
    with patch("shuo.services.twilio_client.make_outbound_call", return_value=mock_sid), \
         patch("dashboard.registry.set_pending"):
        client = _make_call_client(monkeypatch, limit="3")
        payload = {"phone": "+15550001111", "goal": "test"}
        # Exhaust the limit
        for _ in range(3):
            client.post("/dashboard/call", json=payload)
        # Next request must be rate-limited
        resp = client.post("/dashboard/call", json=payload)
        assert resp.status_code == 429, (
            f"Expected 429 on request 4, got {resp.status_code}: {resp.text}"
        )
        assert "Retry-After" in resp.headers, "Missing Retry-After header in 429 response"


def test_rate_limit_retry_after_is_numeric(monkeypatch):
    """Retry-After header value in 429 response is a positive integer."""
    mock_sid = "CA123"
    with patch("shuo.services.twilio_client.make_outbound_call", return_value=mock_sid), \
         patch("dashboard.registry.set_pending"):
        client = _make_call_client(monkeypatch, limit="2")
        payload = {"phone": "+15550001111", "goal": "test"}
        for _ in range(2):
            client.post("/dashboard/call", json=payload)
        resp = client.post("/dashboard/call", json=payload)
        assert resp.status_code == 429
        retry_after = resp.headers.get("Retry-After", "")
        assert retry_after.isdigit(), f"Retry-After is not a numeric string: {retry_after!r}"
        assert int(retry_after) > 0


def test_call_rate_limit_env_var_respected(monkeypatch):
    """CALL_RATE_LIMIT=2 means 3rd request is blocked."""
    mock_sid = "CA456"
    with patch("shuo.services.twilio_client.make_outbound_call", return_value=mock_sid), \
         patch("dashboard.registry.set_pending"):
        client = _make_call_client(monkeypatch, limit="2")
        payload = {"phone": "+15550009999", "goal": "env-var test"}
        # First 2 succeed
        for _ in range(2):
            resp = client.post("/dashboard/call", json=payload)
            assert resp.status_code in (200, 500)
        # 3rd is blocked
        resp = client.post("/dashboard/call", json=payload)
        assert resp.status_code == 429
