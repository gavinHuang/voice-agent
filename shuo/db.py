"""
db.py — PostgreSQL database integration.

Connection pool, schema init, user profile CRUD, and call log CRUD.
Set DATABASE_URL (and optionally DATABASE_PASSWORD for [database_password]
placeholder substitution) in the environment to enable.
"""

import asyncio
import json
import os
import urllib.parse
import datetime
from typing import Optional

import asyncpg

_pool: Optional[asyncpg.Pool] = None


def _get_dsn() -> Optional[str]:
    """Resolve the database DSN, substituting [database_password] if present."""
    url = os.getenv("DATABASE_URL", "")
    if not url:
        return None
    if "[database_password]" in url:
        password = os.getenv("DATABASE_PASSWORD", "")
        url = url.replace("[database_password]", urllib.parse.quote(password, safe=""))
    return url


async def init_pool() -> None:
    """Initialize the connection pool and create tables."""
    global _pool
    dsn = _get_dsn()
    if not dsn:
        return
    try:
        _pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=10,
            # Abort any DB command that takes longer than 8 s — prevents the
            # event loop from stalling when Supabase is slow or unreachable.
            command_timeout=8.0,
            # Proactively retire idle connections after 60 s so we never hand
            # out a stale connection that Supabase/PgBouncer already closed
            # (PgBouncer default server_idle_timeout is 600 s).
            max_inactive_connection_lifetime=60.0,
            # Disable prepared-statement cache — required for Supabase because
            # it uses PgBouncer in transaction-pooling mode, which does not
            # support server-side prepared statements across connections.
            statement_cache_size=0,
        )
        await _create_tables()
        from .log import get_logger
        get_logger("shuo.db").info("Database pool initialized")
    except Exception as e:
        from .log import get_logger
        get_logger("shuo.db").error(f"Database connection failed: {e}")
        _pool = None


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def is_available() -> bool:
    return _pool is not None


async def _create_tables() -> None:
    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                google_id VARCHAR(255) UNIQUE NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL,
                name VARCHAR(255),
                picture VARCHAR(1024),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS call_logs (
                call_id VARCHAR(255) PRIMARY KEY,
                report_id VARCHAR(255),
                tenant_id VARCHAR(255) NOT NULL DEFAULT 'default',
                goal TEXT,
                phone_number VARCHAR(50),
                started_at TIMESTAMPTZ,
                ended_at TIMESTAMPTZ,
                duration_s FLOAT,
                call_disposition VARCHAR(50),
                goal_achieved BOOLEAN,
                outcome_summary TEXT,
                total_turns INTEGER DEFAULT 0,
                barge_in_count INTEGER DEFAULT 0,
                agent_name VARCHAR(255),
                agent_role VARCHAR(255),
                agent_tone VARCHAR(255),
                caller_name VARCHAR(255),
                caller_context TEXT,
                report_json JSONB,
                generated_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_call_logs_tenant_started
            ON call_logs (tenant_id, started_at DESC NULLS LAST)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_call_logs_started
            ON call_logs (started_at DESC NULLS LAST)
        """)


# ── User operations ──────────────────────────────────────────────────────────

async def upsert_user(google_id: str, email: str, name: str, picture: str) -> dict:
    """Create or update a user record. Returns the user as a dict."""
    async with asyncio.timeout(10.0):
        async with _pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO users (google_id, email, name, picture)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (google_id) DO UPDATE
                SET email = EXCLUDED.email,
                    name = EXCLUDED.name,
                    picture = EXCLUDED.picture,
                    updated_at = NOW()
                RETURNING id::text, google_id, email, name, picture,
                          created_at, updated_at
            """, google_id, email, name, picture)
            d = dict(row)
            d["created_at"] = d["created_at"].isoformat()
            d["updated_at"] = d["updated_at"].isoformat()
            return d


async def get_user_by_google_id(google_id: str) -> Optional[dict]:
    """Return user dict or None."""
    async with asyncio.timeout(10.0):
        async with _pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id::text, google_id, email, name, picture, created_at, updated_at "
                "FROM users WHERE google_id = $1",
                google_id,
            )
            if not row:
                return None
            d = dict(row)
            d["created_at"] = d["created_at"].isoformat()
            d["updated_at"] = d["updated_at"].isoformat()
            return d


# ── Call log operations ──────────────────────────────────────────────────────

def _parse_ts(ts_str: Optional[str]) -> Optional[datetime.datetime]:
    if not ts_str:
        return None
    try:
        return datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return None


async def save_call_log(report: dict) -> None:
    """Upsert a full call report into the call_logs table."""
    transport = report.get("transport", {})
    async with asyncio.timeout(10.0):
        async with _pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO call_logs (
                    call_id, report_id, tenant_id, goal, phone_number,
                    started_at, ended_at, duration_s, call_disposition,
                    goal_achieved, outcome_summary, total_turns, barge_in_count,
                    agent_name, agent_role, agent_tone, caller_name, caller_context,
                    report_json, generated_at
                ) VALUES (
                    $1,$2,$3,$4,$5,
                    $6,$7,$8,$9,
                    $10,$11,$12,$13,
                    $14,$15,$16,$17,$18,
                    $19,$20
                )
                ON CONFLICT (call_id) DO UPDATE SET
                    report_id      = EXCLUDED.report_id,
                    goal           = EXCLUDED.goal,
                    ended_at       = EXCLUDED.ended_at,
                    duration_s     = EXCLUDED.duration_s,
                    call_disposition = EXCLUDED.call_disposition,
                    goal_achieved  = EXCLUDED.goal_achieved,
                    outcome_summary = EXCLUDED.outcome_summary,
                    total_turns    = EXCLUDED.total_turns,
                    barge_in_count = EXCLUDED.barge_in_count,
                    report_json    = EXCLUDED.report_json,
                    generated_at   = EXCLUDED.generated_at
            """,
                report.get("call_id", ""),
                report.get("report_id", ""),
                report.get("tenant_id", "default"),
                report.get("goal", ""),
                transport.get("phone_number", ""),
                _parse_ts(transport.get("started_at")),
                _parse_ts(transport.get("ended_at")),
                transport.get("duration_s"),
                report.get("call_disposition", ""),
                report.get("goal_achieved"),
                report.get("outcome_summary"),
                transport.get("total_turns", 0),
                transport.get("barge_in_count", 0),
                report.get("agent_name"),
                report.get("agent_role", ""),
                report.get("agent_tone", ""),
                report.get("caller_name"),
                report.get("caller_context"),
                json.dumps(report),
                _parse_ts(report.get("generated_at")),
            )


async def get_call_log(call_id: str, tenant_id: str = "default") -> Optional[dict]:
    """Return full report JSON for a call, or None."""
    async with asyncio.timeout(10.0):
        async with _pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT report_json FROM call_logs WHERE call_id = $1 AND tenant_id = $2",
                call_id, tenant_id,
            )
            return json.loads(row["report_json"]) if row else None


async def list_call_logs(tenant_id: Optional[str] = None, limit: int = 100) -> list:
    """Return lightweight call log summaries, newest first."""
    async with asyncio.timeout(10.0):
        async with _pool.acquire() as conn:
            if tenant_id:
                rows = await conn.fetch("""
                    SELECT call_id, tenant_id, phone_number, started_at, ended_at,
                           duration_s, goal, call_disposition, goal_achieved,
                           outcome_summary, total_turns, barge_in_count, report_id, generated_at
                    FROM call_logs
                    WHERE tenant_id = $1
                    ORDER BY started_at DESC NULLS LAST
                    LIMIT $2
                """, tenant_id, limit)
            else:
                rows = await conn.fetch("""
                    SELECT call_id, tenant_id, phone_number, started_at, ended_at,
                           duration_s, goal, call_disposition, goal_achieved,
                           outcome_summary, total_turns, barge_in_count, report_id, generated_at
                    FROM call_logs
                    ORDER BY started_at DESC NULLS LAST
                    LIMIT $1
                """, limit)

            results = []
            for row in rows:
                d = dict(row)
                for key in ("started_at", "ended_at", "generated_at"):
                    if d.get(key) is not None:
                        d[key] = d[key].isoformat()
                results.append(d)
            return results
