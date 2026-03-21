---
phase: 02-bug-fixes
verified: 2026-03-21T00:00:00Z
status: passed
score: 7/7 must-haves verified
re_verification: false
---

# Phase 2: Bug Fixes Verification Report

**Phase Goal:** The four known correctness issues are eliminated so subsequent phases build on a stable foundation
**Verified:** 2026-03-21
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| #  | Truth                                                                        | Status     | Evidence                                                                                 |
|----|------------------------------------------------------------------------------|------------|------------------------------------------------------------------------------------------|
| 1  | Concurrent DTMF events on the same call cannot corrupt _dtmf_pending         | VERIFIED   | `_dtmf_lock = asyncio.Lock()` at module level; both `on_dtmf` and `get_saved_state` use `async with _dtmf_lock`; 50-concurrent-writer test passes |
| 2  | TTS pool never dispenses an entry that has already been evicted/cancelled     | VERIFIED   | `self._lock = asyncio.Lock()` in `TTSPool.__init__`; `get()`, `_evict_stale()`, `stop()` all hold lock during list mutations only; `cancel()` always runs outside lock |
| 3  | All 8 bug-fix tests exist and all 8 pass GREEN                               | VERIFIED   | `shuo/tests/test_bug_fixes.py` has 304 lines, 8 `def test_` functions; `python3 -m pytest shuo/tests/test_bug_fixes.py` → 8 passed |
| 4  | A slow token observer callback does not stall the LLM stream                 | VERIFIED   | `asyncio.get_event_loop().call_soon(self._on_token_observed, clean_text)` at agent.py:334; bare blocking call removed; `test_token_observer_nonblocking` GREEN |
| 5  | A call with no activity for N seconds is automatically hung up               | VERIFIED   | `_inactivity_watchdog` coroutine in conversation.py puts `HangupRequestEvent` on queue after timeout; `test_inactivity_watchdog_fires` GREEN |
| 6  | The inactivity timeout is configurable via CALL_INACTIVITY_TIMEOUT env var   | VERIFIED   | `CALL_INACTIVITY_TIMEOUT = float(os.getenv("CALL_INACTIVITY_TIMEOUT", "300"))` at conversation.py:41; `test_inactivity_timeout_env_var` GREEN |
| 7  | The watchdog task is cancelled cleanly on normal call termination             | VERIFIED   | `watchdog.cancel()` + `await watchdog` + `except asyncio.CancelledError: pass` in finally block at conversation.py:296-300; `test_watchdog_cancelled_on_stop` GREEN |

**Score:** 7/7 truths verified

---

### Required Artifacts

| Artifact                                    | Expected                                              | Status   | Details                                                                                              |
|---------------------------------------------|-------------------------------------------------------|----------|------------------------------------------------------------------------------------------------------|
| `shuo/tests/test_bug_fixes.py`              | Test scaffold for all four bug fixes (8 test cases)   | VERIFIED | 304 lines; 8 test functions; all 8 pass; covers BUG-01 through BUG-04                               |
| `shuo/shuo/server.py`                       | asyncio.Lock protecting _dtmf_pending                 | VERIFIED | `_dtmf_lock: asyncio.Lock = asyncio.Lock()` at line 61; used at lines 629 and 640                   |
| `shuo/shuo/services/tts_pool.py`            | asyncio.Lock protecting self._ready list              | VERIFIED | `self._lock = asyncio.Lock()` at line 63; `async with self._lock` at lines 91, 130, 180              |
| `shuo/shuo/agent.py`                        | Non-blocking token observer via call_soon             | VERIFIED | `call_soon(self._on_token_observed, clean_text)` at line 334; bare blocking call absent              |
| `shuo/shuo/conversation.py`                 | Inactivity watchdog task and CALL_INACTIVITY_TIMEOUT  | VERIFIED | `CALL_INACTIVITY_TIMEOUT` at line 41; `async def _inactivity_watchdog` at line 44; 8 references total |

---

### Key Link Verification

| From                              | To                              | Via                                                              | Status   | Details                                                                                        |
|-----------------------------------|---------------------------------|------------------------------------------------------------------|----------|-----------------------------------------------------------------------------------------------|
| `shuo/shuo/server.py`             | `shuo/shuo/conversation.py`     | `get_saved_state` becomes `async def`, call site uses `await`    | WIRED    | `async def get_saved_state` at server.py:637; `await get_saved_state(event.call_sid)` at conversation.py:154; `Awaitable` in type hint at conversation.py:76 |
| `shuo/shuo/services/tts_pool.py`  | `shuo/shuo/services/tts_pool.py`| Lock held only during list mutation, released before cancel()    | WIRED    | All three `async with self._lock` blocks release before `await entry.tts.cancel()`; confirmed by code inspection at lines 91-108, 130-134, 180-193 |
| `shuo/shuo/agent.py`              | asyncio event loop              | `asyncio.get_event_loop().call_soon(self._on_token_observed, ...)`| WIRED   | Pattern present at agent.py:334; bare `self._on_token_observed(clean_text)` absent             |
| `shuo/shuo/conversation.py`       | event_queue                     | watchdog puts HangupRequestEvent after timeout                   | WIRED    | `await event_queue.put(HangupRequestEvent())` at conversation.py:63 inside `_inactivity_watchdog` |
| `shuo/shuo/conversation.py`       | finally block                   | watchdog.cancel() in finally                                     | WIRED    | `watchdog.cancel()` at conversation.py:296 inside the finally block; awaited with CancelledError caught |

---

### Requirements Coverage

| Requirement | Source Plan | Description                                                              | Status    | Evidence                                                                                           |
|-------------|------------|--------------------------------------------------------------------------|-----------|----------------------------------------------------------------------------------------------------|
| BUG-01      | 02-01      | `_dtmf_pending` dict access is protected by an asyncio lock              | SATISFIED | `_dtmf_lock` at server.py:61; `async with _dtmf_lock` at lines 629, 640; 2 tests GREEN            |
| BUG-02      | 02-01      | TTS pool eviction is atomic (TOCTOU race eliminated)                     | SATISFIED | `self._lock` at tts_pool.py:63; collect-then-cancel pattern; `async with self._lock` in get/evict/stop; 2 tests GREEN |
| BUG-03      | 02-02      | Token observer callback runs in a non-blocking context                   | SATISFIED | `call_soon(self._on_token_observed, ...)` at agent.py:334; 1 test GREEN                            |
| BUG-04      | 02-02      | Calls with no activity for N seconds are automatically hung up           | SATISFIED | `_inactivity_watchdog` + `CALL_INACTIVITY_TIMEOUT` in conversation.py; watchdog cancelled in finally; 3 tests GREEN |

No orphaned requirements: REQUIREMENTS.md maps exactly BUG-01 through BUG-04 to Phase 2, all four are claimed by plans and all four are satisfied.

---

### Anti-Patterns Found

No blockers or warnings found. Scan of all five modified files (`test_bug_fixes.py`, `server.py`, `tts_pool.py`, `agent.py`, `conversation.py`) returned no TODO/FIXME/PLACEHOLDER comments, no empty implementations, and no stub return patterns.

---

### Human Verification Required

None. All behaviors are verifiable programmatically:

- Concurrency correctness is covered by the 50-writer test and the double-cancel test (both pass).
- Non-blocking behavior is verified by the wall-clock timing test (`test_token_observer_nonblocking`).
- Watchdog firing and cancellation are verified by dedicated tests.
- No visual, real-time, or external-service behaviors are in scope for this phase.

---

### Commit Verification

All five commits documented in the SUMMARYs exist in the repository and are on the current branch (`feature/refactor`):

| Commit  | Message                                                    |
|---------|------------------------------------------------------------|
| bb08a97 | test(02-01): add failing test scaffold for BUG-01 through BUG-04 |
| 73faef2 | feat(02-01): fix BUG-01 — protect _dtmf_pending with asyncio.Lock |
| 00ef5da | feat(02-01): fix BUG-02 — atomic TTS pool eviction with asyncio.Lock |
| 8daa1e1 | fix(02-02): BUG-03 — non-blocking token observer via call_soon |
| 2758d66 | feat(02-02): BUG-04 — inactivity watchdog with configurable timeout |

---

### Final Test Run

```
python3 -m pytest shuo/tests/ -q --tb=no
42 passed, 4 warnings in 1.28s
```

8 bug-fix tests all GREEN. 34 pre-existing tests (test_update.py, test_isp.py, test_ivr_barge_in.py) all pass. Zero regressions.

---

### Gaps Summary

No gaps. All seven observable truths are verified, all five artifacts exist and are substantive, all five key links are wired, all four requirements are satisfied, no anti-patterns were detected.

---

_Verified: 2026-03-21_
_Verifier: Claude (gsd-verifier)_
