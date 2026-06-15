"""
db.py — Supabase database integration via REST API (PostgREST).

Uses httpx to talk to the Supabase PostgREST endpoint — no direct TCP
connection required, so it works even when the database host is IPv6-only
and the local machine lacks IPv6 routing.

Required environment variables:
    SUPABASE_URL   e.g. https://<project-ref>.supabase.co
    SUPABASE_KEY   service-role key (bypasses RLS)

Optional fallback (unused but kept for reference):
    DATABASE_URL / DATABASE_PASSWORD
"""

import json
import os
import datetime
from typing import Optional

import httpx

_client: Optional[httpx.AsyncClient] = None
_supabase_url: str = ""
_supabase_key: str = ""


def _rest(path: str) -> str:
    return f"{_supabase_url}/rest/v1{path}"


def _headers() -> dict:
    return {
        "apikey": _supabase_key,
        "Authorization": f"Bearer {_supabase_key}",
        "Content-Type": "application/json",
    }


async def init_pool() -> None:
    """Initialize the HTTP client and verify connectivity."""
    global _client, _supabase_url, _supabase_key
    _supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    _supabase_key = os.getenv("SUPABASE_KEY", "")
    if not _supabase_url or not _supabase_key:
        from .log import get_logger
        get_logger("shuo.db").warning(
            "SUPABASE_URL / SUPABASE_KEY not set — database disabled"
        )
        return
    _client = httpx.AsyncClient(timeout=10.0)
    try:
        resp = await _client.get(_rest("/users?select=count"), headers=_headers())
        resp.raise_for_status()
        from .log import get_logger
        get_logger("shuo.db").info("Supabase REST client initialised")
    except Exception as e:
        from .log import get_logger
        get_logger("shuo.db").error(f"Supabase connectivity check failed: {e}")
        await _client.aclose()
        _client = None


async def close_pool() -> None:
    global _client
    if _client:
        await _client.aclose()
        _client = None


def is_available() -> bool:
    return _client is not None


# ── User operations ──────────────────────────────────────────────────────────

async def upsert_user(google_id: str, email: str, name: str, picture: str) -> dict:
    """Create or update a user record. Returns the user as a dict."""
    resp = await _client.post(
        _rest("/users"),
        headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
        json={"google_id": google_id, "email": email, "name": name, "picture": picture},
    )
    resp.raise_for_status()
    row = resp.json()[0]
    # Normalise timestamps to plain ISO strings
    for k in ("created_at", "updated_at"):
        if row.get(k):
            row[k] = row[k]
    return row


async def get_user_by_google_id(google_id: str) -> Optional[dict]:
    """Return user dict or None."""
    resp = await _client.get(
        _rest(f"/users?google_id=eq.{google_id}&select=*"),
        headers=_headers(),
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


# ── Call log operations ──────────────────────────────────────────────────────

def _parse_ts(ts_str: Optional[str]) -> Optional[str]:
    """Pass timestamps through as ISO strings (PostgREST accepts them)."""
    if not ts_str:
        return None
    try:
        # Normalise Z suffix
        return ts_str.replace("Z", "+00:00")
    except Exception:
        return None


async def save_call_log(report: dict) -> None:
    """Upsert a full call report into the call_logs table."""
    transport = report.get("transport", {})
    payload = {
        "call_id":          report.get("call_id", ""),
        "report_id":        report.get("report_id", ""),
        "tenant_id":        report.get("tenant_id", "default"),
        "goal":             report.get("goal", ""),
        "phone_number":     transport.get("phone_number", ""),
        "started_at":       _parse_ts(transport.get("started_at")),
        "ended_at":         _parse_ts(transport.get("ended_at")),
        "duration_s":       transport.get("duration_s"),
        "call_disposition": report.get("call_disposition", ""),
        "goal_achieved":    report.get("goal_achieved"),
        "outcome_summary":  report.get("outcome_summary"),
        "total_turns":      transport.get("total_turns", 0),
        "barge_in_count":   transport.get("barge_in_count", 0),
        "agent_name":       report.get("agent_name"),
        "agent_role":       report.get("agent_role", ""),
        "agent_tone":       report.get("agent_tone", ""),
        "caller_name":      report.get("caller_name"),
        "caller_context":   report.get("caller_context"),
        "report_json":      report,
        "generated_at":     _parse_ts(report.get("generated_at")),
    }
    # Remove None values so PostgREST uses column defaults
    payload = {k: v for k, v in payload.items() if v is not None}
    resp = await _client.post(
        _rest("/call_logs"),
        headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
        json=payload,
    )
    resp.raise_for_status()


async def get_call_log(call_id: str, tenant_id: str = "default") -> Optional[dict]:
    """Return full report JSON for a call, or None."""
    resp = await _client.get(
        _rest(f"/call_logs?call_id=eq.{call_id}&tenant_id=eq.{tenant_id}&select=report_json"),
        headers=_headers(),
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None
    rj = rows[0].get("report_json")
    return rj if isinstance(rj, dict) else json.loads(rj)


async def list_call_logs(tenant_id: Optional[str] = None, limit: int = 100) -> list:
    """Return lightweight call log summaries, newest first."""
    fields = (
        "call_id,tenant_id,phone_number,started_at,ended_at,"
        "duration_s,goal,call_disposition,goal_achieved,"
        "outcome_summary,total_turns,barge_in_count,report_id,generated_at"
    )
    if tenant_id:
        url = _rest(
            f"/call_logs?tenant_id=eq.{tenant_id}"
            f"&order=started_at.desc.nullslast&limit={limit}&select={fields}"
        )
    else:
        url = _rest(
            f"/call_logs?order=started_at.desc.nullslast&limit={limit}&select={fields}"
        )
    resp = await _client.get(url, headers=_headers())
    resp.raise_for_status()
    return resp.json()
