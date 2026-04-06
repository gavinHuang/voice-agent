"""
Tests for Twilio webhook signature validation (Task 1) and trace file rotation (Task 2).

Phase 05 Plan 02 — SEC-02, SEC-04
"""

import asyncio
import os
import time
import pytest
from unittest.mock import patch, MagicMock


# =============================================================================
# Task 1: Twilio signature validation on webhook routes
# =============================================================================

@pytest.fixture
def twilio_client(monkeypatch):
    """TestClient with TWILIO_AUTH_TOKEN set to enable validation."""
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("TWILIO_PUBLIC_URL", "https://example.ngrok.io")
    from fastapi.testclient import TestClient
    from shuo.web import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def twilio_client_no_token(monkeypatch):
    """TestClient with no TWILIO_AUTH_TOKEN — validation should be skipped."""
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("TWILIO_PUBLIC_URL", "https://example.ngrok.io")
    from fastapi.testclient import TestClient
    from shuo.web import app
    return TestClient(app, raise_server_exceptions=False)


def test_twilio_post_twiml_valid_signature(monkeypatch, twilio_client):
    """POST /twiml with a valid Twilio signature returns 200 (XML content)."""
    monkeypatch.setattr(
        "shuo.web.RequestValidator.validate",
        lambda self, url, params, sig: True,
    )
    resp = twilio_client.post(
        "/twiml",
        headers={"X-Twilio-Signature": "valid-sig"},
    )
    assert resp.status_code == 200
    assert "xml" in resp.headers.get("content-type", "")


def test_twilio_post_twiml_missing_signature(monkeypatch, twilio_client):
    """POST /twiml without X-Twilio-Signature header returns 403."""
    resp = twilio_client.post("/twiml")
    assert resp.status_code == 403
    assert resp.json() == {"detail": "Invalid Twilio signature"}


def test_twilio_post_twiml_wrong_signature(monkeypatch, twilio_client):
    """POST /twiml with wrong X-Twilio-Signature returns 403."""
    monkeypatch.setattr(
        "shuo.web.RequestValidator.validate",
        lambda self, url, params, sig: False,
    )
    resp = twilio_client.post(
        "/twiml",
        headers={"X-Twilio-Signature": "bad-sig"},
    )
    assert resp.status_code == 403
    assert resp.json() == {"detail": "Invalid Twilio signature"}


def test_twilio_no_auth_token_skips_validation(monkeypatch, twilio_client_no_token):
    """When TWILIO_AUTH_TOKEN is unset, signature validation is skipped."""
    resp = twilio_client_no_token.post("/twiml")
    # Should process normally — no 403 for missing signature
    assert resp.status_code == 200


def test_twilio_post_ivr_dtmf_valid_signature(monkeypatch, twilio_client):
    """POST /twiml/ivr-dtmf?digit=1 with valid signature returns 200."""
    monkeypatch.setattr(
        "shuo.web.RequestValidator.validate",
        lambda self, url, params, sig: True,
    )
    resp = twilio_client.post(
        "/twiml/ivr-dtmf?digit=1",
        headers={"X-Twilio-Signature": "valid-sig"},
    )
    assert resp.status_code == 200


def test_twilio_post_ivr_dtmf_no_signature(monkeypatch, twilio_client):
    """POST /twiml/ivr-dtmf?digit=1 without signature returns 403."""
    resp = twilio_client.post("/twiml/ivr-dtmf?digit=1")
    assert resp.status_code == 403
    assert resp.json() == {"detail": "Invalid Twilio signature"}


# =============================================================================
# Task 2: Trace file rotation with configurable limits
# =============================================================================

def _make_trace_file(directory, name, age_seconds=0):
    """Create a fake .json trace file with controlled mtime."""
    p = directory / name
    p.write_text('{"call_id": "' + name.replace(".json", "") + '"}')
    mtime = time.time() - age_seconds
    os.utime(p, (mtime, mtime))
    return p


def test_cleanup_traces_deletes_old_files(tmp_path, monkeypatch):
    """cleanup_traces deletes files older than max_age_hours."""
    import shuo.tracer as tracer_module
    monkeypatch.setattr(tracer_module, "TRACE_DIR", tmp_path)

    from shuo.tracer import cleanup_traces

    old_file = _make_trace_file(tmp_path, "old.json", age_seconds=7200)  # 2 hours old
    new_file = _make_trace_file(tmp_path, "new.json", age_seconds=60)    # 1 minute old

    deleted = cleanup_traces(max_age_hours=1.0, max_files=100)

    assert deleted == 1
    assert not old_file.exists(), "Old file should have been deleted"
    assert new_file.exists(), "New file should NOT have been deleted"


def test_cleanup_traces_caps_file_count(tmp_path, monkeypatch):
    """cleanup_traces deletes oldest files when count exceeds max_files (keeps newest)."""
    import shuo.tracer as tracer_module
    monkeypatch.setattr(tracer_module, "TRACE_DIR", tmp_path)

    from shuo.tracer import cleanup_traces

    # Create 5 files with increasing age
    files = []
    for i in range(5):
        f = _make_trace_file(tmp_path, f"trace-{i}.json", age_seconds=(5 - i) * 60)
        files.append(f)

    # Cap at 2 files (keep newest 2), max_age set very high so nothing deleted by age
    deleted = cleanup_traces(max_age_hours=100.0, max_files=2)

    assert deleted == 3
    # Oldest 3 should be gone, newest 2 should remain
    remaining = list(tmp_path.glob("*.json"))
    assert len(remaining) == 2


def test_cleanup_traces_age_then_count(tmp_path, monkeypatch):
    """cleanup_traces applies age deletion first, then count cap."""
    import shuo.tracer as tracer_module
    monkeypatch.setattr(tracer_module, "TRACE_DIR", tmp_path)

    from shuo.tracer import cleanup_traces

    # 3 old files (2h+), 2 fresh files
    for i in range(3):
        _make_trace_file(tmp_path, f"old-{i}.json", age_seconds=7200)
    for i in range(2):
        _make_trace_file(tmp_path, f"new-{i}.json", age_seconds=60)

    # Age limit 1h kills the 3 old ones; count limit 1 kills 1 more (the older new one)
    deleted = cleanup_traces(max_age_hours=1.0, max_files=1)

    assert deleted == 4
    remaining = list(tmp_path.glob("*.json"))
    assert len(remaining) == 1


def test_cleanup_traces_no_directory(monkeypatch):
    """cleanup_traces does nothing when directory does not exist."""
    from pathlib import Path
    import shuo.tracer as tracer_module
    monkeypatch.setattr(tracer_module, "TRACE_DIR", Path("/tmp/shuo-nonexistent-xyz"))

    from shuo.tracer import cleanup_traces
    deleted = cleanup_traces(max_age_hours=1.0, max_files=100)
    assert deleted == 0


def test_cleanup_traces_defaults_from_env(tmp_path, monkeypatch):
    """Env vars TRACE_MAX_FILES and TRACE_MAX_AGE_HOURS override defaults."""
    import shuo.tracer as tracer_module
    monkeypatch.setattr(tracer_module, "TRACE_DIR", tmp_path)
    monkeypatch.setenv("TRACE_MAX_FILES", "1")
    monkeypatch.setenv("TRACE_MAX_AGE_HOURS", "999")

    from shuo.tracer import cleanup_traces

    # 3 files, all fresh (age limit won't touch them), but max_files=1 from env
    for i in range(3):
        _make_trace_file(tmp_path, f"trace-{i}.json", age_seconds=(3 - i) * 60)

    deleted = cleanup_traces()  # Use env-derived defaults
    assert deleted == 2
    remaining = list(tmp_path.glob("*.json"))
    assert len(remaining) == 1


def test_cleanup_traces_defaults_100_files_24h(monkeypatch):
    """TRACE_MAX_FILES=100 and TRACE_MAX_AGE_HOURS=24 are defaults when env not set."""
    monkeypatch.delenv("TRACE_MAX_FILES", raising=False)
    monkeypatch.delenv("TRACE_MAX_AGE_HOURS", raising=False)

    from shuo.tracer import cleanup_traces
    import inspect

    # Check that the function uses int(..., "100") and float(..., "24")
    src = inspect.getsource(cleanup_traces)
    assert '"100"' in src or "'100'" in src, "Default TRACE_MAX_FILES=100 not found in source"
    assert '"24"' in src or "'24'" in src, "Default TRACE_MAX_AGE_HOURS=24 not found in source"
