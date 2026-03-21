---
phase: 05-security-hardening
plan: 02
subsystem: server, tracer
tags: [security, twilio, webhook-validation, trace-rotation]
dependency_graph:
  requires: []
  provides: [twilio-signature-validation, trace-file-rotation]
  affects: [shuo/shuo/server.py, shuo/shuo/tracer.py]
tech_stack:
  added: [twilio.request_validator.RequestValidator]
  patterns: [FastAPI Depends(), form-body extraction for POST validation]
key_files:
  created: [shuo/tests/test_webhook_security.py]
  modified: [shuo/shuo/server.py, shuo/shuo/tracer.py]
decisions:
  - "verify_twilio_signature reads form body for POST routes: dial-action carries Twilio form params that must be included in signature computation"
  - "cleanup_traces applies age filter then count cap: ensures both constraints enforced independently"
  - "Module import path is shuo.tracer (not shuo.shuo.tracer): the shuo/shuo/ directory is the package root"
metrics:
  duration: 10min
  completed: 2026-03-22
  tasks_completed: 2
  files_modified: 3
---

# Phase 05 Plan 02: Twilio Webhook Security and Trace Rotation Summary

JWT auth with Twilio RequestValidator on all webhook routes plus configurable trace file rotation at server startup.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | Failing tests for signature validation + trace cleanup | a864b9c | shuo/tests/test_webhook_security.py |
| 1 (GREEN) | Twilio signature validation on webhook routes | b1e97d9 | shuo/shuo/server.py |
| 2 (GREEN) | Trace file rotation with configurable limits | 0ce2bff | shuo/shuo/tracer.py, shuo/shuo/server.py, shuo/tests/test_webhook_security.py |

## What Was Built

**Task 1 — Twilio webhook signature validation:**

- `verify_twilio_signature` async FastAPI dependency added to `shuo/shuo/server.py`
- Reads `TWILIO_AUTH_TOKEN`; if unset, skips validation (dev-friendly)
- Reconstructs full public URL from `TWILIO_PUBLIC_URL` env var for correct signing
- For POST requests with `application/x-www-form-urlencoded` content type, extracts the form body dict and passes it to `RequestValidator.validate()` — this is required for `/twiml/dial-action/{call_id}` which receives real Twilio form callbacks
- Applied via `dependencies=[Depends(verify_twilio_signature)]` to four routes: `/twiml`, `/twiml/conference/{call_id}`, `/twiml/dial-action/{call_id}`, `/twiml/ivr-dtmf`
- WebSocket routes (`/ws`, `/ws-listen`) intentionally excluded (Twilio does not sign WebSocket upgrades)

**Task 2 — Trace file rotation:**

- `cleanup_traces(max_files, max_age_hours)` function added to `shuo/shuo/tracer.py`
- Phase 1: deletes files with mtime older than `TRACE_MAX_AGE_HOURS` (default 24h)
- Phase 2: caps total count to `TRACE_MAX_FILES` (default 100) by removing oldest first
- Called from `_warmup()` in `shuo/shuo/server.py` so cleanup runs at every server startup

## Decisions Made

- **form body in POST validation**: `dial-action` route receives `CallStatus`, `DialCallStatus`, etc. as form params. Twilio signs against these params, so an empty dict `{}` would always fail validation. The dependency now conditionally extracts form data for `application/x-www-form-urlencoded` POST requests.
- **cleanup order: age then count**: Age filter runs first, removing definitively stale files. Count cap runs second on remaining files, keeping the newest N. Both constraints enforced independently.
- **Module import path correction**: Tests initially used `shuo.shuo.tracer` but the installed package root is `shuo/shuo/`, so the correct import is `shuo.tracer`. Fixed inline as Rule 1 bug.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed incorrect module import path in test**
- **Found during:** Task 2 GREEN phase
- **Issue:** Test used `import shuo.shuo.tracer as tracer_module` — raises `ModuleNotFoundError` because the package structure places `shuo/shuo/` as the package root, making the correct path `shuo.tracer`
- **Fix:** Replaced all `shuo.shuo.tracer` with `shuo.tracer` in test file
- **Files modified:** shuo/tests/test_webhook_security.py
- **Commit:** 0ce2bff

## Test Results

- 12 new tests in `shuo/tests/test_webhook_security.py` — all pass
- 105 total tests across test suite — all pass, no regressions

## Self-Check: PASSED
