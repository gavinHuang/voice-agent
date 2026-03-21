---
phase: 02-bug-fixes
plan: "02"
subsystem: agent, conversation
tags: [bug-fix, asyncio, call-soon, watchdog, tdd]
dependency_graph:
  requires: [02-01]
  provides: [non-blocking-token-observer, inactivity-watchdog, CALL_INACTIVITY_TIMEOUT]
  affects: [shuo/shuo/agent.py, shuo/shuo/conversation.py, shuo/tests/test_bug_fixes.py]
tech_stack:
  added: []
  patterns: [call-soon-dispatch, asyncio-watchdog-task, collect-then-cancel, optional-mutable-list-state]
key_files:
  created: []
  modified:
    - shuo/shuo/agent.py
    - shuo/shuo/conversation.py
    - shuo/tests/test_bug_fixes.py
decisions:
  - "call_soon used instead of run_in_executor: observer is sync, no thread needed; runs in next event-loop turn on same thread"
  - "last_activity defaults to None in _inactivity_watchdog: tests pass 2-arg form; production passes 3-arg form with shared list"
  - "watchdog sleep interval is min(5.0, timeout): avoids 5s wait when timeout < 5s (test uses 0.1s)"
  - "MediaEvent excluded from last_activity update: silent-but-connected calls must still time out"
metrics:
  duration: "2 min"
  completed: "2026-03-21"
  tasks_completed: 2
  files_modified: 3
---

# Phase 2 Plan 2: BUG-03/BUG-04 Fixes Summary

**One-liner:** asyncio.call_soon defers token observer to next event-loop turn, and an inactivity watchdog task auto-hangs-up stalled calls after CALL_INACTIVITY_TIMEOUT seconds.

## What Was Built

1. **BUG-03 fix:** `asyncio.get_event_loop().call_soon(self._on_token_observed, clean_text)` in `agent.py` replaces the bare synchronous call. The observer still runs on the same thread/loop but in a future turn, so `_on_llm_token` returns immediately regardless of observer duration.

2. **BUG-04 fix:** `_inactivity_watchdog` coroutine in `conversation.py` runs as a background task from StreamStartEvent. It polls every `min(5, timeout)` seconds and puts `HangupRequestEvent` on the queue if `last_activity[0]` hasn't been updated within `timeout` seconds. The `CALL_INACTIVITY_TIMEOUT` module-level constant defaults to 300s and is overridable via env var. The watchdog is cancelled cleanly in the finally block.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Fix BUG-03 — Non-blocking token observer via call_soon | 8daa1e1 | shuo/shuo/agent.py, shuo/tests/test_bug_fixes.py |
| 2 | Fix BUG-04 — Inactivity watchdog with configurable timeout | 2758d66 | shuo/shuo/conversation.py |

## Test Results

| Test | Status | Notes |
|------|--------|-------|
| test_dtmf_pending_sequential | GREEN | Pre-existing (BUG-01) |
| test_dtmf_lock_concurrent | GREEN | Pre-existing (BUG-01) |
| test_tts_pool_eviction_atomic | GREEN | Pre-existing (BUG-02) |
| test_tts_pool_concurrent_evict | GREEN | Pre-existing (BUG-02) |
| test_token_observer_nonblocking | GREEN | BUG-03 fixed |
| test_inactivity_watchdog_fires | GREEN | BUG-04 fixed |
| test_watchdog_cancelled_on_stop | GREEN | BUG-04 fixed |
| test_inactivity_timeout_env_var | GREEN | BUG-04 fixed |

Pre-existing tests: 34 tests all pass (test_update.py, test_isp.py, test_ivr_barge_in.py)
Total: 42 passed, 0 failed.

## Decisions Made

1. **call_soon vs run_in_executor:** `call_soon` schedules a synchronous callable on the next event-loop iteration without spawning a thread. Since all current observers are synchronous and lightweight, no thread pool needed. Future async observers would require a different approach.

2. **last_activity optional parameter:** The plan specified a mandatory 3-argument signature, but the tests call with 2 args `(queue, timeout)`. Made `last_activity` optional with `None` default, initialized internally if not provided. Production code passes the shared list explicitly.

3. **Watchdog sleep interval `min(5.0, timeout)`:** The production default is 5s polling. For test timeouts of 0.1s, sleeping 5s first would make the test take 5s. Using `min(5.0, timeout)` ensures the test fires quickly without changing production behavior.

4. **MediaEvent excluded from activity updates:** Silent-but-connected calls that stream audio silence (MediaEvents) should still time out. Only meaningful signaling events reset the timer.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test setup missing required Agent attributes**
- **Found during:** Task 1 verification
- **Issue:** `Agent.__new__(Agent)` bypasses `__init__`, so `_got_first_token`, `_dtmf_queue`, `_tracer`, `_turn`, and `_t0` were absent. `_on_llm_token` accesses these before the observer call site, causing `AttributeError`.
- **Fix:** Added the 5 missing attributes to the test's manual agent setup with appropriate mock/default values. Set `_got_first_token = True` to skip the first-token tracing path.
- **Files modified:** shuo/tests/test_bug_fixes.py
- **Commit:** 8daa1e1

**2. [Rule 2 - Design] _inactivity_watchdog signature adapted for test compatibility**
- **Found during:** Task 2 implementation
- **Issue:** Plan specified 3-arg signature but tests call with 2 args; if `last_activity` is mandatory, tests would fail with `TypeError`.
- **Fix:** Made `last_activity` optional (`Optional[list] = None`), initialized internally when not provided.
- **Files modified:** shuo/shuo/conversation.py
- **Commit:** 2758d66

## Verification

```
python3 -m pytest shuo/tests/ -v
# 42 passed, 0 failed, 4 warnings

grep -c "call_soon" shuo/shuo/agent.py          # → 1
grep -c "_inactivity_watchdog" shuo/shuo/conversation.py  # → 4
grep -c "CALL_INACTIVITY_TIMEOUT" shuo/shuo/conversation.py  # → 2
grep -c "watchdog.cancel" shuo/shuo/conversation.py  # → 1
```

## Self-Check: PASSED
