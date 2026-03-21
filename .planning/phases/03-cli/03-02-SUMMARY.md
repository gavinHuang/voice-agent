---
phase: 03-cli
plan: 02
subsystem: cli
tags: [click, asyncio, local-isp, run_conversation, transcript]

# Dependency graph
requires:
  - phase: 03-01
    provides: CLI group with serve/call/bench subcommands
  - phase: 01-isp-abstraction
    provides: LocalISP.pair() and run_conversation() interfaces
provides:
  - local-call subcommand running two concurrent LLM conversations via LocalISP
  - _make_observer for labelled [CALLER]/[CALLEE] transcript printing
  - _build_goal for folding identity into goal string
  - Config merge from local_call.caller/callee YAML sections with flag overrides
affects: [04-bench, 05-supervisor]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "asyncio.wait(FIRST_COMPLETED) to terminate concurrent tasks on first hangup"
    - "Per-subcommand env check (local-call only needs DG/GROQ/EL, not Twilio)"
    - "Deferred import of LocalISP and run_conversation inside coroutine body"

key-files:
  created: []
  modified:
    - shuo/shuo/cli.py
    - shuo/tests/test_cli.py

key-decisions:
  - "asyncio.wait(FIRST_COMPLETED) used to terminate concurrent tasks on first hangup — cleaner than polling a shared flag"
  - "Deferred imports inside _run_local_call coroutine keep top-level imports lightweight and avoid circular import risk"
  - "Per-subcommand env check for local-call: only DEEPGRAM/GROQ/ELEVENLABS required (no Twilio); separate from _check_env_vars()"

patterns-established:
  - "_make_observer(label) pattern: reusable observer factory for any transcript-printing consumer"
  - "get_goal=lambda _: goal_str: closure captures built goal string; call_sid ignored for local calls"

requirements-completed: [CLI-03]

# Metrics
duration: 2min
completed: 2026-03-21
---

# Phase 3 Plan 02: local-call Subcommand Summary

**voice-agent local-call runs two concurrent LLM agents in-process via LocalISP with live [CALLER]/[CALLEE] transcript printing and FIRST_COMPLETED termination**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-21T17:51:59Z
- **Completed:** 2026-03-21T17:53:39Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- Added `local-call` subcommand to CLI group with `--caller-goal`, `--caller-identity`, `--callee-goal`, `--callee-identity` flags
- Two LocalISP instances are paired via `LocalISP.pair()` and driven by concurrent `run_conversation()` tasks; call ends when either side completes
- Live transcript printed with `[CALLER]` and `[CALLEE]` labels via reusable `_make_observer(label)` factory
- Identity folded into goal string via `_build_goal(goal, identity)` helper
- Config merged from `local_call.caller`/`local_call.callee` YAML sections; CLI flags override config
- 6 new tests covering help, concurrent run, config merge, flag override, env check, and identity-in-goal

## Task Commits

1. **Task 1: Implement local-call subcommand** - `e739429` (feat)
2. **Task 2: Tests for local-call subcommand** - `535eb90` (test)

## Files Created/Modified

- `shuo/shuo/cli.py` - Added `_make_observer`, `_build_goal`, `_run_local_call`, `local_call` command, `_check_local_call_env_vars`
- `shuo/tests/test_cli.py` - Added 6 local-call tests

## Decisions Made

- `asyncio.wait(FIRST_COMPLETED)` used to terminate concurrent tasks on first hangup — cleaner than polling a shared flag
- Deferred imports inside `_run_local_call` coroutine keep top-level imports lightweight and avoid circular import risk
- Per-subcommand env check for `local-call`: only DEEPGRAM/GROQ/ELEVENLABS required (no Twilio); separate helper `_check_local_call_env_vars` added

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- CLI-03 complete: `voice-agent local-call` delivers end-to-end in-process two-agent conversation
- Phase 03-cli is now complete (all three subcommands: serve, call, local-call)
- Ready for Phase 04 (bench) or Phase 05 (supervisor) depending on roadmap priority

---
*Phase: 03-cli*
*Completed: 2026-03-21*
