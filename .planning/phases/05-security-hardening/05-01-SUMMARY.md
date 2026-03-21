---
phase: 05-security-hardening
plan: "01"
subsystem: auth
tags: [fastapi, api-key, rate-limiting, websocket, dashboard]

# Dependency graph
requires:
  - phase: 04-ivr-benchmark
    provides: dashboard/server.py route structure used as base for auth additions
provides:
  - verify_api_key FastAPI dependency (X-API-Key header guard for all HTTP dashboard routes)
  - WebSocket token auth via ?token= query param with code 4003 rejection
  - _RateLimiter sliding-window class limiting POST /call by IP per minute
  - CALL_RATE_LIMIT env var for configurable rate cap (default 10)
  - DASHBOARD_API_KEY env var enabling/disabling auth (empty = disabled)
  - shuo/tests/test_dashboard_auth.py with 13 tests covering auth and rate limiting
affects: [06-pydantic-ai-migration, deployment, dashboard-consumers]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - FastAPI Depends() for declarative route-level auth without per-handler boilerplate
    - WebSocket auth via Query param (headers unavailable at WS handshake in browser)
    - In-process sliding-window rate limiter using monotonic clock and per-IP deque
    - Env-var gate pattern for auth (empty var = dev mode off, set = enforced)

key-files:
  created:
    - shuo/tests/test_dashboard_auth.py
  modified:
    - dashboard/server.py

key-decisions:
  - "verify_api_key as FastAPI Depends() instead of middleware: route-scoped, skips WebSocket naturally"
  - "WebSocket close code 4003 (not 403): WebSocket protocol; close before accept avoids partial handshake"
  - "In-process _RateLimiter instead of slowapi: slowapi not installed; simple sliding window sufficient"
  - "autouse fixture resets _call_limiter._hits between tests: module-level limiter retains state across test functions"
  - "DASHBOARD_API_KEY empty string treated as disabled: zero-config dev experience"

patterns-established:
  - "Depends(verify_api_key) on all HTTP route decorators except WebSocket"
  - "WebSocket auth: close(code=4003) before accept() when token invalid"
  - "Rate limiter reset fixture for tests sharing module-level state"

requirements-completed: [SEC-01, SEC-03]

# Metrics
duration: 5min
completed: 2026-03-22
---

# Phase 5 Plan 01: Dashboard Security Hardening Summary

**API key auth on all dashboard HTTP routes via FastAPI Depends(), WebSocket token gating with close code 4003, and in-process sliding-window rate limiter on POST /call with Retry-After header**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-22T00:00:00Z
- **Completed:** 2026-03-22T00:05:00Z
- **Tasks:** 2 (TDD: RED commit + GREEN commit)
- **Files modified:** 2

## Accomplishments
- All 8 dashboard HTTP routes protected by X-API-Key header check; return 401 without valid key
- WebSocket /dashboard/ws rejects unauthenticated connections with code 4003 before accepting
- Auth disabled automatically when DASHBOARD_API_KEY env var is unset (developer-friendly)
- POST /call rate-limited at CALL_RATE_LIMIT (default 10) requests/minute per client IP; 429 with Retry-After on excess
- 13 new tests covering all auth and rate-limiting scenarios; all existing 93 tests still pass

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Dashboard API key auth + WebSocket token auth (tests)** - `a3ff888` (test)
2. **Task 1+2 GREEN: Dashboard API key auth + rate limiting implementation** - `f3332d0` (feat)

_Note: TDD tasks — RED (failing tests) committed first, GREEN (implementation) committed second_

## Files Created/Modified
- `dashboard/server.py` - Added verify_api_key dependency, Depends() on all routes, WebSocket token check, _RateLimiter class, rate-limit logic in start_call
- `shuo/tests/test_dashboard_auth.py` - 13 tests: 401 without key, 200 with key, auth-disabled mode, WebSocket accept/reject, rate limit enforcement, Retry-After header, CALL_RATE_LIMIT env override

## Decisions Made
- Used `Depends(verify_api_key)` on route decorators rather than middleware: cleaner scope, WebSocket route excluded naturally
- WebSocket close code 4003 (custom close code): distinguishes auth rejection from other errors
- Implemented `_RateLimiter` in-process instead of slowapi (not installed): simple sliding window adequate for single-process deployment
- Added `autouse=True` pytest fixture to reset module-level `_call_limiter._hits` between tests: prevents cross-test pollution

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added autouse fixture to reset rate limiter state between tests**
- **Found during:** Task 2 (rate limit tests) — `test_call_rate_limit_env_var_respected` failed when run with other rate tests due to shared module-level limiter
- **Issue:** `_call_limiter` is module-level; hits from one test accumulate and cause subsequent tests to fail
- **Fix:** Added `@pytest.fixture(autouse=True) def reset_rate_limiter()` to clear `_hits` before/after each test
- **Files modified:** shuo/tests/test_dashboard_auth.py
- **Verification:** All 13 tests pass both in isolation and together
- **Committed in:** f3332d0 (Task 1+2 GREEN commit)

---

**Total deviations:** 1 auto-fixed (1 missing critical for test correctness)
**Impact on plan:** Auto-fix necessary for test reliability. No scope creep.

## Issues Encountered
None — implementation matched plan exactly. Rate limiter test isolation required one fixture addition.

## User Setup Required

**Environment variables to configure when deploying:**

```bash
# Enable dashboard authentication (leave unset in dev for auth-disabled mode)
DASHBOARD_API_KEY=your-secret-key-here

# Optional: tune call rate limit (default: 10 per minute per IP)
CALL_RATE_LIMIT=10
```

No external services required — auth is in-process.

## Next Phase Readiness
- Dashboard security hardening complete; all routes protected
- Phase 06 (pydantic-ai migration) can proceed — dashboard auth is independent of agent framework
- Production deployment requires setting DASHBOARD_API_KEY env var

---
*Phase: 05-security-hardening*
*Completed: 2026-03-22*
