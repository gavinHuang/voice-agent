# Phase 5: Security Hardening - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

Protect the dashboard and call endpoints against unauthorized access, spoofed webhooks, and resource abuse. Four controls: dashboard auth, Twilio webhook signature validation, `/call` rate limiting, and trace file rotation. No user-facing changes — this is backend hardening only.

</domain>

<decisions>
## Implementation Decisions

### Dashboard authentication (SEC-01)

- **Simple API key auth** — the dashboard is for local/internal use only; no sessions, OAuth, or JWTs needed
- **Key configured via env var** — `DASHBOARD_API_KEY` in `.env` / environment; unauthenticated requests get 401
- All details (header name, query param fallback, how the WebSocket authenticates) are **Claude's discretion** — keep it as simple as possible given the local-only use case

### Claude's Discretion

- HTTP header name for the API key (e.g., `X-API-Key`, `Authorization: Bearer`, or query param `?token=`)
- Whether the dashboard HTML page stores and sends the key automatically (e.g., from `localStorage` or a prompt)
- WebSocket auth mechanism (query param on the WS URL is the standard approach for browser WebSocket)
- Twilio signature scope — which endpoints require `X-Twilio-Signature` validation (at minimum: `/twiml`, `/twiml/*`, and `/ws`; IVR mock endpoints are separate)
- Rate limit value and window for `/call` endpoint (a sensible default like 10 requests/minute per IP is expected)
- Whether rate limit is configurable via env var (`CALL_RATE_LIMIT`, `CALL_RATE_WINDOW`) or hardcoded
- Trace rotation strategy: max file count, max age, or both; when cleanup runs (startup, periodic background task, or per-call)
- Whether `DASHBOARD_API_KEY` being unset disables auth entirely (dev-friendly) or fails closed (more secure)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Security requirements
- `.planning/REQUIREMENTS.md` §Security — SEC-01 through SEC-04: formal requirements with acceptance criteria

### Dashboard (primary target for auth)
- `dashboard/server.py` — FastAPI router with all dashboard routes + `POST /call`; auth middleware goes here
- `dashboard/__init__.py` — package init; check for any existing middleware

### Main server (webhook validation target)
- `shuo/shuo/server.py` — FastAPI app; `/twiml`, `/ws`, and other Twilio webhook endpoints
- Comments in server.py identify which routes are Twilio webhooks vs internal

### Trace file target
- `shuo/shuo/tracer.py` — `TRACE_DIR = Path("/tmp/shuo")`; `Tracer.save()` writes `<call_id>.json`; cleanup logic goes here or in server startup

### Auth reference (PROJECT.md constraint)
- `.planning/PROJECT.md` — "OAuth for dashboard: Simple token auth is sufficient for internal tooling"

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `dashboard/server.py` `APIRouter` — middleware or dependency injection can be added at the router level without touching `shuo/shuo/server.py`
- `shuo/shuo/server.py` `@app.on_event("startup")` (`_warmup`) — trace cleanup can be triggered here as a background task
- Twilio SDK already imported (`from twilio.rest import Client`) — `twilio.request_validator.RequestValidator` is in the same package at no extra cost

### Established Patterns
- FastAPI `Depends()` for auth — standard pattern; a `verify_api_key` dependency injected into protected routes
- `os.getenv()` for all credentials — consistent with every other env var in the codebase (TWILIO_*, DEEPGRAM_*, GROQ_*)
- `asyncio.create_task()` for background work — used in conversation.py; same pattern for periodic trace cleanup

### Integration Points
- `dashboard/server.py` — every route in this router needs auth (GET `/`, WS `/ws`, GET `/calls`, POST `/call`, POST `/calls/{id}/hangup`, etc.)
- `shuo/shuo/server.py` — Twilio webhook routes: `POST /twiml`, `POST /twiml/conference/{call_id}`, `POST /twiml/dial-action/{call_id}`, `POST /twiml/ivr-dtmf`, `WebSocket /ws`
- `shuo/shuo/tracer.py` `TRACE_DIR` — cleanup reads this directory; no other code needs to change

</code_context>

<specifics>
## Specific Ideas

- Dashboard is for **local usage only** — auth can be simple (API key in env), no need for user management, sessions, or token rotation

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 05-security-hardening*
*Context gathered: 2026-03-22*
