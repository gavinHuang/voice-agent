---
phase: 02-bug-fixes
plan: "01"
subsystem: server, tts-pool, conversation
tags: [bug-fix, race-condition, asyncio, tdd]
dependency_graph:
  requires: []
  provides: [_dtmf_lock, TTSPool._lock, async-get_saved_state]
  affects: [shuo/shuo/server.py, shuo/shuo/services/tts_pool.py, shuo/shuo/conversation.py]
tech_stack:
  added: []
  patterns: [collect-then-cancel, lazy-lock-init, tdd-red-green]
key_files:
  created:
    - shuo/tests/test_bug_fixes.py
  modified:
    - shuo/shuo/server.py
    - shuo/shuo/services/tts_pool.py
    - shuo/shuo/conversation.py
decisions:
  - "_dtmf_lock initialized at module level (not in _warmup) — test environment doesn't run FastAPI startup"
  - "cancel() calls always outside asyncio.Lock scope — avoids serializing I/O under lock"
  - "on_dtmf converted to async def to support async with _dtmf_lock"
metrics:
  duration: "4 min"
  completed: "2026-03-21"
  tasks_completed: 3
  files_modified: 4
---

# Phase 2 Plan 1: TDD Test Scaffold + BUG-01/BUG-02 Fixes Summary

**One-liner:** asyncio.Lock serializes concurrent _dtmf_pending access in server.py and _ready list access in TTSPool, eliminating silent data-loss race conditions.

## What Was Built

Written and verified:
1. Full 8-test scaffold in `test_bug_fixes.py` covering all four bug fixes (all RED at creation)
2. BUG-01 fix: `_dtmf_lock = asyncio.Lock()` at module level; both `on_dtmf` (now async) and `get_saved_state` (now async def) use `async with _dtmf_lock`
3. BUG-02 fix: `self._lock = asyncio.Lock()` in TTSPool; `get()`, `_evict_stale()`, `stop()` hold lock only during list mutations, cancel() always runs outside lock

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Write test scaffold (8 tests, all RED) | bb08a97 | shuo/tests/test_bug_fixes.py |
| 2 | Fix BUG-01 — _dtmf_pending lock | 73faef2 | shuo/shuo/server.py, shuo/shuo/conversation.py |
| 3 | Fix BUG-02 — TTS pool lock | 00ef5da | shuo/shuo/services/tts_pool.py |

## Test Results

| Test | Status | Notes |
|------|--------|-------|
| test_dtmf_pending_sequential | GREEN | Baseline sequential access |
| test_dtmf_lock_concurrent | GREEN | 50 concurrent writers, no data loss |
| test_tts_pool_eviction_atomic | GREEN | Evicted entries never returned by get() |
| test_tts_pool_concurrent_evict | GREEN | No double-cancel under concurrent evict/get |
| test_token_observer_nonblocking | RED | Expected — BUG-03 fix in next plan |
| test_inactivity_watchdog_fires | RED | Expected — BUG-04 fix in next plan |
| test_watchdog_cancelled_on_stop | RED | Expected — BUG-04 fix in next plan |
| test_inactivity_timeout_env_var | RED | Expected — BUG-04 fix in next plan |

Pre-existing tests: 34 tests all pass (test_update.py, test_isp.py, test_ivr_barge_in.py)

## Decisions Made

1. **_dtmf_lock at module level (not in _warmup):** Plan specified _warmup init to avoid Python <3.10 issues, but test environment has no FastAPI startup. Module-level `asyncio.Lock()` is safe in Python 3.10+ (which the codebase uses — `dict | None` syntax confirms this).

2. **cancel() always outside lock:** The collect-then-cancel pattern ensures the lock is never held during I/O awaits. This prevents lock contention from blocking the fill loop.

3. **on_dtmf converted to async def:** Required to support `async with _dtmf_lock`. The closure is called inside an async context (conversation loop) so this is safe.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] _dtmf_lock initialization moved from _warmup to module level**
- **Found during:** Task 2 verification
- **Issue:** Test imports shuo.server without running FastAPI startup, so _warmup() never fires. Test would fail `isinstance(_dtmf_lock, asyncio.Lock)` if lock is None.
- **Fix:** Initialize `_dtmf_lock = asyncio.Lock()` at module level. Removed _warmup re-init (no longer needed). Python 3.10+ confirmed safe by existing `dict | None` union syntax in codebase.
- **Files modified:** shuo/shuo/server.py
- **Commit:** 73faef2

## Verification

```
python3 -m pytest shuo/tests/ -q --tb=no
# 38 passed, 4 failed (BUG-03/04 intentionally RED)
grep -c "_dtmf_lock" shuo/shuo/server.py  # → 3
grep -c "self._lock" shuo/shuo/services/tts_pool.py  # → 4
grep -c "async with self._lock" shuo/shuo/services/tts_pool.py  # → 3
grep "await get_saved_state" shuo/shuo/conversation.py  # → 1 match
```

## Self-Check: PASSED
