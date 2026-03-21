---
phase: 01-isp-abstraction
plan: "03"
subsystem: api
tags: [asyncio, protocol, typing, telephony, isp, twilio, refactor]

# Dependency graph
requires:
  - ISP Protocol class (from plan 01-01)
  - TwilioISP (from plan 01-02)
  - Agent accepting isp parameter (from plan 01-02)
provides:
  - run_conversation(isp, ...) — ISP-injected main event loop
  - TwilioISP wired into server.py WebSocket endpoint
  - test_ivr_barge_in.py using MockISP (full ISP Protocol mock)
affects:
  - Any caller of run_conversation_over_twilio (now renamed to run_conversation)
  - LocalISP can now be wired to run_conversation for CLI local-call mode

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "ISP callbacks: on_isp_media/on_isp_start/on_isp_stop replace inline read_twilio() reader task"
    - "isp._inject hook: conversation loop exposes event_queue.put_nowait for LocalISP/MockISP DTMF injection"
    - "on_dtmf/on_hangup as bookkeeping-only: actual transport actions delegated to ISP methods"

key-files:
  created: []
  modified:
    - shuo/shuo/conversation.py
    - shuo/shuo/server.py
    - shuo/tests/test_ivr_barge_in.py

key-decisions:
  - "on_hangup not passed to run_conversation from server.py — TwilioISP.hangup() handles REST call entirely; server has no additional hangup bookkeeping"
  - "isp.stop() called in both HangupRequestEvent path and finally block — idempotent; TwilioISP.stop() is safe to call twice"
  - "MockISP fires on_start() synchronously during start() — simpler than async task, sufficient for test assertions"

patterns-established:
  - "ISP abstraction complete: conversation.py accepts any ISP; server.py wires TwilioISP; tests use MockISP"
  - "Callback registration pattern: isp.start(on_media, on_start, on_stop) replaces raw WebSocket reader tasks in conversation loop"

requirements-completed: [ISP-04, ISP-05]

# Metrics
duration: 4min
completed: 2026-03-21
---

# Phase 1 Plan 03: Wire ISP into conversation.py and server.py Summary

**run_conversation() accepts any ISP; TwilioISP wired in server.py; barge-in tests use MockISP; ISP abstraction complete**

## Performance

- **Duration:** ~4 min
- **Started:** 2026-03-21T09:22:19Z
- **Completed:** 2026-03-21T09:25:44Z
- **Tasks:** 2
- **Files modified:** 3 modified

## Accomplishments

- conversation.py renamed `run_conversation_over_twilio` -> `run_conversation`; first parameter changed from `websocket: WebSocket` to `isp`; removed `read_twilio()` inner function, `fastapi.WebSocket` import, `parse_twilio_message` import, and `json` import; ISP callbacks (`on_isp_media`, `on_isp_start`, `on_isp_stop`) registered via `await isp.start()`; `isp._inject` hook set for LocalISP/MockISP DTMF injection; Agent constructed with `isp=isp`; DTMFToneEvent dispatches `isp.send_dtmf()`; HangupRequestEvent dispatches `isp.hangup()` then `isp.stop()`; finally block calls `isp.stop()` (idempotent)
- server.py updated: imports `run_conversation` and `TwilioISP`; `isp = TwilioISP(websocket)` created before call; `on_hangup` reduced to no-op (TwilioISP.hangup() handles REST); `on_dtmf` saves history to `_dtmf_pending` only (REST redirect moved to TwilioISP.send_dtmf()); call site passes `isp` as first argument
- test_ivr_barge_in.py: `MockWebSocket` and `_twilio_msg` helper removed; `MockISP` class added implementing all 7 ISP Protocol methods; `push_stop()` triggers `on_stop()` callback registered during `start()`; both barge-in tests updated to create `MockISP` and call `run_conversation(mock_isp, ...)`
- All 34 tests pass: 24 state machine tests + 2 barge-in integration tests + 8 ISP unit tests

## Task Commits

Each task was committed atomically:

1. **Task 1: Refactor conversation.py to use ISP** - `5c223d2` (feat)
2. **Task 2: Update server.py wiring and test_ivr_barge_in.py** - `be161b8` (feat)

## Files Created/Modified

- `shuo/shuo/conversation.py` (modified) - Fully decoupled from Twilio: ISP parameter, callback registration, DTMF/hangup via ISP, reader task replaced by isp.start()
- `shuo/shuo/server.py` (modified) - TwilioISP wired at call site; on_dtmf/on_hangup simplified to bookkeeping only
- `shuo/tests/test_ivr_barge_in.py` (modified) - MockISP replaces MockWebSocket; both tests use run_conversation with ISP interface

## Decisions Made

- `on_hangup` not passed to `run_conversation` from server.py: TwilioISP.hangup() handles the REST call entirely; server has no additional hangup bookkeeping needed
- `isp.stop()` called in both HangupRequestEvent path and finally block: idempotent by design; TwilioISP cancels task once, subsequent calls are no-ops
- MockISP fires `on_start()` synchronously during `start()`: simpler than spawning a task, and sufficient for test assertions (no need to simulate async stream startup delay)

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- ISP abstraction is complete: `run_conversation` accepts any ISP implementation — TwilioISP (production), LocalISP (local-call mode), or MockISP (tests)
- All requirements ISP-04 and ISP-05 are satisfied
- Phase 1 complete: no file outside `services/twilio_isp.py` imports `fastapi.WebSocket` for audio/call handling

---
*Phase: 01-isp-abstraction*
*Completed: 2026-03-21*

## Self-Check: PASSED

- shuo/shuo/conversation.py: FOUND
- shuo/shuo/server.py: FOUND
- shuo/tests/test_ivr_barge_in.py: FOUND
- .planning/phases/01-isp-abstraction/01-03-SUMMARY.md: FOUND
- Commit 5c223d2 (Task 1 - conversation.py refactor): FOUND
- Commit be161b8 (Task 2 - server.py + test update): FOUND
