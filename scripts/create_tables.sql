-- create_tables.sql
-- Creates the users and call_logs tables for the dialact voice-agent.
-- Safe to run multiple times (all statements are idempotent).

-- ── Users (Google-authenticated profiles) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    google_id  VARCHAR(255) UNIQUE NOT NULL,
    email      VARCHAR(255) UNIQUE NOT NULL,
    name       VARCHAR(255),
    picture    VARCHAR(1024),
    created_at TIMESTAMPTZ  DEFAULT NOW(),
    updated_at TIMESTAMPTZ  DEFAULT NOW()
);

-- ── Call logs ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS call_logs (
    call_id          VARCHAR(255) PRIMARY KEY,
    report_id        VARCHAR(255),
    tenant_id        VARCHAR(255)  NOT NULL DEFAULT 'default',
    goal             TEXT,
    phone_number     VARCHAR(50),
    started_at       TIMESTAMPTZ,
    ended_at         TIMESTAMPTZ,
    duration_s       FLOAT,
    call_disposition VARCHAR(50),
    goal_achieved    BOOLEAN,
    outcome_summary  TEXT,
    total_turns      INTEGER       DEFAULT 0,
    barge_in_count   INTEGER       DEFAULT 0,
    agent_name       VARCHAR(255),
    agent_role       VARCHAR(255),
    agent_tone       VARCHAR(255),
    caller_name      VARCHAR(255),
    caller_context   TEXT,
    report_json      JSONB,
    generated_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_call_logs_tenant_started
    ON call_logs (tenant_id, started_at DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS idx_call_logs_started
    ON call_logs (started_at DESC NULLS LAST);
