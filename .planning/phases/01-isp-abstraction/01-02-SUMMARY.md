---
phase: 01-isp-abstraction
plan: "02"
subsystem: api
tags: [asyncio, protocol, typing, telephony, isp, twilio, refactor]

# Dependency graph
requires:
  - ISP Protocol class (from plan 01-01)
  - LocalISP (from plan 01-01)
provides:
  - TwilioISP: Twilio WebSocket ISP implementation with REST API methods
  - AudioPlayer decoupled from fastapi.WebSocket (accepts any ISP)
  - Agent decoupled from fastapi.WebSocket (accepts any ISP)
affects:
  - 01-isp-abstraction (plan 03+: CLI local-call mode can now wire LocalISP without WebSocket)
  - shuo/shuo/conversation.py (will need updating to pass TwilioISP instead of WebSocket to Agent)
  - shuo/shuo/server.py (will need updating to create TwilioISP and call isp.start() instead of read_twilio)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "ISP delegation: AudioPlayer and Agent delegate all transport concerns to the ISP instance"
    - "TwilioISP reader task: background asyncio.Task reads WebSocket, dispatches via callbacks"
    - "REST API via asyncio.get_running_loop().run_in_executor: avoid deprecated get_event_loop()"

key-files:
  created:
    - shuo/shuo/services/twilio_isp.py
  modified:
    - shuo/shuo/services/player.py
    - shuo/shuo/agent.py
    - shuo/shuo/services/__init__.py

key-decisions:
  - "stream_sid kept in Agent for context but player no longer needs it for JSON formatting (ISP owns that)"
  - "AudioPlayer constructor keeps stream_sid as optional str='' parameter for backward compatibility in caller sites not yet updated"
  - "TwilioISP._call_sid captured from StreamStartEvent — used by send_dtmf and hangup REST calls"

patterns-established:
  - "ISP delegation pattern: components call isp.send_audio() / isp.send_clear() — transport format is ISP's concern"
  - "TwilioISP owns all Twilio JSON formatting and REST API calls — neither player.py nor agent.py import fastapi"

requirements-completed: [ISP-02, ISP-04]

# Metrics
duration: 2min
completed: 2026-03-21
---

# Phase 1 Plan 02: TwilioISP Implementation and AudioPlayer/Agent Refactor Summary

**TwilioISP wrapping Twilio WebSocket/REST behind the ISP Protocol; AudioPlayer and Agent decoupled from fastapi.WebSocket**

## Performance

- **Duration:** ~2 min
- **Started:** 2026-03-21T09:18:33Z
- **Completed:** 2026-03-21T09:20:18Z
- **Tasks:** 2
- **Files modified:** 1 created, 3 modified

## Accomplishments

- TwilioISP implements all 7 ISP Protocol methods — background reader task parses Twilio WebSocket messages and dispatches via on_media / on_start / on_stop callbacks; send_audio/send_clear format Twilio JSON; send_dtmf/hangup use Twilio REST API via asyncio.get_running_loop()
- AudioPlayer no longer imports fastapi.WebSocket — constructor accepts `isp` parameter; _send_audio and _send_clear delegate entirely to isp.send_audio() and isp.send_clear()
- Agent no longer imports fastapi.WebSocket — constructor accepts `isp` parameter; inject_dtmf calls isp.send_audio() instead of formatting Twilio JSON directly
- services/__init__.py now exports ISP, TwilioISP, and LocalISP alongside existing exports
- 24 state machine tests and 8 ISP tests all pass — zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Create TwilioISP implementation** - `19424d6` (feat)
2. **Task 2: Refactor AudioPlayer and Agent to accept ISP instead of WebSocket** - `b170de3` (feat)

## Files Created/Modified

- `shuo/shuo/services/twilio_isp.py` (created) - TwilioISP: all 7 ISP methods, background WebSocket reader, Twilio JSON formatting, REST API hangup/dtmf/call
- `shuo/shuo/services/player.py` (modified) - Removed WebSocket/json imports, replaced self._websocket with self._isp, _send_audio/_send_clear delegate to ISP
- `shuo/shuo/agent.py` (modified) - Removed WebSocket/json imports, replaced self._websocket with self._isp, inject_dtmf uses isp.send_audio(), AudioPlayer instantiation updated
- `shuo/shuo/services/__init__.py` (modified) - Added ISP, TwilioISP, LocalISP exports

## Decisions Made

- AudioPlayer `stream_sid` parameter kept as optional `str = ""` — the parameter is now unused internally (ISP owns stream_sid for JSON formatting) but keeping it avoids breaking call sites not yet updated
- TwilioISP captures `_call_sid` from `StreamStartEvent` during `_reader()` — available to `send_dtmf()` and `hangup()` without needing it passed at construction time
- `asyncio.get_running_loop()` used in send_dtmf/hangup (not deprecated `get_event_loop()`)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- TwilioISP is ready; conversation.py and server.py still pass WebSocket directly to Agent — those wiring changes come in subsequent plans
- LocalISP is available for local-call mode CLI (Plan 03+)
- Both AudioPlayer and Agent now accept any ISP implementation — the seam is established

---
*Phase: 01-isp-abstraction*
*Completed: 2026-03-21*

## Self-Check: PASSED

- shuo/shuo/services/twilio_isp.py: FOUND
- shuo/shuo/services/player.py: FOUND
- shuo/shuo/agent.py: FOUND
- .planning/phases/01-isp-abstraction/01-02-SUMMARY.md: FOUND
- Commit 19424d6 (Task 1 - TwilioISP): FOUND
- Commit b170de3 (Task 2 - AudioPlayer/Agent refactor): FOUND
