---
phase: 06-agent-framework-migration
plan: "02"
subsystem: agent
tags: [pydantic-ai, marker-scanner, refactor, agent, turn-context]

requires:
  - phase: 06-agent-framework-migration
    plan: "01"
    provides: LLMTurnContext dataclass with dtmf_queue/hold_start/hold_end/hold_continue/hangup_pending

provides:
  - Agent without MarkerScanner — uses LLMService.turn_context for all tool side effects
  - All five events (DTMFToneEvent, HoldStartEvent, HoldEndEvent, HangupPendingEvent, HangupRequestEvent) fire with identical semantics
  - test_bug_fixes.py scanner mock removed; test_token_observer_nonblocking passes cleanly

affects:
  - shuo/shuo/agent.py (primary modification target)
  - shuo/tests/test_bug_fixes.py (scanner mock removed)

tech-stack:
  added: []
  patterns:
    - "Agent._on_llm_done reads self._llm.turn_context after LLM completes — all tool side effects via ctx"
    - "hold_continue checked FIRST in _on_llm_done to override tts_had_text (Pitfall 4)"
    - "_on_llm_token forwards token directly to tts.send() — no scanner.feed()"

key-files:
  created: []
  modified:
    - shuo/shuo/agent.py (MarkerScanner deleted, _on_llm_token simplified, _on_llm_done rewritten)
    - shuo/tests/test_bug_fixes.py (scanner mock removed from test_token_observer_nonblocking)

key-decisions:
  - "server.py needs no changes: agent.history returns List[ModelMessage] stored in-process dict; restore_history calls llm.set_history which accepts List[ModelMessage] — type-consistent throughout"
  - "history and restore_history typed as generic list: avoids importing pydantic-ai types into agent.py while remaining compatible with LLMService.set_history"
  - "hold_continue path in _on_llm_done cancels TTS and ends turn silently — identical semantics to old HOLD_CONTINUE marker path"

requirements-completed:
  - AGENT-03
  - AGENT-04

duration: 2min
completed: 2026-03-22
---

# Phase 6 Plan 02: MarkerScanner Removal Summary

**MarkerScanner class deleted from agent.py; Agent._on_llm_done now reads tool side effects from LLMService.turn_context, preserving all event semantics with 113 tests passing**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-21T22:49:03Z
- **Completed:** 2026-03-22T00:50:42Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- Deleted the 65-line `MarkerScanner` class and its `MARKER SCANNER` section from `agent.py`
- Removed `_scanner`, `_pending_hold_start`, `_pending_hold_end` fields from Agent
- Simplified `_on_llm_token`: token forwarded directly to `tts.send()` (no scanning)
- Rewrote `_on_llm_done`: reads `ctx = self._llm.turn_context` for all tool side effects
- `hold_continue` checked FIRST to override `tts_had_text` (Pitfall 4 from research)
- All five events (DTMFToneEvent, HoldStartEvent, HoldEndEvent, HangupPendingEvent, HangupRequestEvent) fire with identical semantics
- Removed scanner mock from `test_token_observer_nonblocking` — test passes without it
- Full suite: 113 tests pass (including `test_marker_scanner_deleted` and `test_agent_no_marker_fields` now GREEN)

## Task Commits

1. **Task 1: Remove MarkerScanner and wire Agent to LLMTurnContext** - `b07b70b` (feat)
2. **Task 2: Update test_bug_fixes.py scanner mock and run full regression** - `9320f5c` (feat)

## Files Created/Modified

- `shuo/shuo/agent.py` (modified) - MarkerScanner deleted; _on_llm_token and _on_llm_done rewritten
- `shuo/tests/test_bug_fixes.py` (modified) - scanner mock removed from test_token_observer_nonblocking

## Decisions Made

- **server.py unchanged**: `agent.history` returns `List[ModelMessage]` stored in the in-process `_dtmf_pending` dict and passed back via `agent.restore_history()` -> `llm.set_history()`. All accepting `List[ModelMessage]` — no serialization, no type mismatch. No code change required.
- **Generic `list` type hints**: `Agent.history` and `restore_history(saved_history)` annotated as `list` (not `List[ModelMessage]`) to avoid importing pydantic-ai types into `agent.py`. The underlying type is correct; the annotation is intentionally loose.
- **hold_continue path**: Cancels TTS and emits `AgentTurnDoneEvent` immediately — identical behavior to the old `HOLD_CONTINUE` branch that also cancelled TTS and emitted `AgentTurnDoneEvent`.

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None.

## Next Phase Readiness

- Phase 6 is complete: AGENT-01 through AGENT-05 all satisfied
- `MarkerScanner` is gone; all tool effects flow through `LLMTurnContext`
- Full test suite green (113 tests)

---
*Phase: 06-agent-framework-migration*
*Completed: 2026-03-22*

## Self-Check: PASSED

All files found and all commits verified.
