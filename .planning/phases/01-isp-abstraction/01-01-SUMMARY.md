---
phase: 01-isp-abstraction
plan: "01"
subsystem: api
tags: [asyncio, protocol, typing, testing, telephony, isp]

# Dependency graph
requires: []
provides:
  - ISP Protocol class (7 async methods) defining the telephony backend contract
  - LocalISP in-process implementation with asyncio.Queue-based audio routing
  - 8 unit tests covering Protocol shape, audio routing, DTMF, hangup, and stream lifecycle
affects:
  - 01-isp-abstraction (plans 02+: TwilioISP refactor, CLI local-call mode)
  - phase 6 (pydantic-ai agent framework — agents will receive LocalISP for local testing)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "ISP structural typing: implementations satisfy ISP Protocol without explicit inheritance"
    - "Queue-based in-process routing: asyncio.Queue with None sentinel for clean shutdown"
    - "TDD Red-Green: test stubs committed before implementation"

key-files:
  created:
    - shuo/shuo/services/isp.py
    - shuo/shuo/services/local_isp.py
    - shuo/tests/test_isp.py
  modified: []

key-decisions:
  - "ISP Protocol uses Python structural typing (Protocol class) — no ABC inheritance, duck typing with type hints is sufficient"
  - "on_media callback receives decoded bytes (not base64 str) — decoding happens inside send_audio"
  - "DTMF injection uses _inject callable set externally — conversation loop owns event routing, LocalISP does not"
  - "stop() uses None sentinel on queue to unblock and exit reader task cleanly"

patterns-established:
  - "LocalISP pair pattern: pair(a, b) at construction time, start() registers callbacks independently"
  - "Callback signature for on_start: (stream_sid: str, call_sid: str, phone: str) — positional strings matching StreamStartEvent fields"

requirements-completed: [ISP-01, ISP-03]

# Metrics
duration: 1min
completed: 2026-03-21
---

# Phase 1 Plan 01: ISP Protocol and LocalISP Summary

**ISP Protocol (7 async methods) and LocalISP (asyncio.Queue audio routing, DTMF injection, pair/start/stop lifecycle) with 8 passing unit tests**

## Performance

- **Duration:** ~1 min
- **Started:** 2026-03-21T09:14:44Z
- **Completed:** 2026-03-21T09:15:22Z
- **Tasks:** 2 (TDD: RED + GREEN)
- **Files modified:** 3 created, 0 modified

## Accomplishments

- ISP Protocol class with exactly 7 async methods establishes the telephony backend contract for all current and future implementations
- LocalISP delivers in-process audio routing via asyncio queues — enables fully synchronous unit testing of conversation logic without real telephony
- 8 unit tests cover all specified behaviors: Protocol shape, audio routing, DTMF delivery, hangup signaling, send_clear no-op, and reader task lifecycle
- Zero regressions: all 24 existing test_update.py state machine tests continue to pass

## Task Commits

Each task was committed atomically:

1. **Task 1: Define ISP Protocol and write test stubs (RED)** - `3c35c34` (test)
2. **Task 2: Implement LocalISP (GREEN)** - `eb689a7` (feat)

_Note: TDD tasks have two commits — failing tests first, then implementation._

## Files Created/Modified

- `shuo/shuo/services/isp.py` - ISP Protocol class with 7 async method signatures
- `shuo/shuo/services/local_isp.py` - LocalISP: pair(), start(), stop(), send_audio(), send_clear(), send_dtmf(), hangup(), call()
- `shuo/tests/test_isp.py` - 8 unit tests covering Protocol shape and all LocalISP behaviors

## Decisions Made

- ISP Protocol uses Python structural typing (`Protocol` class) — no ABC inheritance needed; duck typing with type hints is sufficient for correctness
- `on_media` callback receives decoded `bytes` (not base64 str) — decoding happens inside `send_audio`, keeping callers type-clean
- DTMF injection uses `_inject` callable set externally by the conversation loop — LocalISP does not own event routing
- `stop()` sends a `None` sentinel to the queue to unblock `_reader()` and exit cleanly, avoiding `task.cancel()` / CancelledError handling

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- ISP Protocol and LocalISP are ready for use by subsequent plans in Phase 1
- Plan 02 can refactor TwilioISP to satisfy the ISP Protocol (the 26 existing TwilioISP tests will be the regression guard)
- Local-call mode CLI (Plan 03+) can use LocalISP.pair() to wire two agents together in-process

---
*Phase: 01-isp-abstraction*
*Completed: 2026-03-21*

## Self-Check: PASSED

- shuo/shuo/services/isp.py: FOUND
- shuo/shuo/services/local_isp.py: FOUND
- shuo/tests/test_isp.py: FOUND
- .planning/phases/01-isp-abstraction/01-01-SUMMARY.md: FOUND
- Commit 3c35c34 (RED): FOUND
- Commit eb689a7 (GREEN): FOUND
