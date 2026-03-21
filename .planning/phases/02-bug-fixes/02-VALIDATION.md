---
phase: 2
slug: bug-fixes
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-21
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio |
| **Config file** | none detected — add `asyncio_mode = auto` to pytest config |
| **Quick run command** | `python3 -m pytest shuo/tests/ -x -q --tb=short` |
| **Full suite command** | `python3 -m pytest shuo/tests/ -v` |
| **Estimated runtime** | ~3 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest shuo/tests/ -x -q --tb=short`
- **After every plan wave:** Run `python3 -m pytest shuo/tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green (34 pre-existing + new bug fix tests)
- **Max feedback latency:** ~5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| Wave 0 stub | TBD | 0 | BUG-01–04 | unit | `pytest shuo/tests/test_bug_fixes.py -x` | ❌ Wave 0 | ⬜ pending |
| BUG-01 fix | TBD | 1+ | BUG-01 | unit (asyncio) | `pytest shuo/tests/test_bug_fixes.py::test_dtmf_lock_concurrent -x` | ❌ Wave 0 | ⬜ pending |
| BUG-01 fix | TBD | 1+ | BUG-01 | unit | `pytest shuo/tests/test_bug_fixes.py::test_dtmf_pending_sequential -x` | ❌ Wave 0 | ⬜ pending |
| BUG-02 fix | TBD | 1+ | BUG-02 | unit (asyncio) | `pytest shuo/tests/test_bug_fixes.py::test_tts_pool_eviction_atomic -x` | ❌ Wave 0 | ⬜ pending |
| BUG-02 fix | TBD | 1+ | BUG-02 | unit (asyncio) | `pytest shuo/tests/test_bug_fixes.py::test_tts_pool_concurrent_evict -x` | ❌ Wave 0 | ⬜ pending |
| BUG-03 fix | TBD | 1+ | BUG-03 | unit (asyncio, timing) | `pytest shuo/tests/test_bug_fixes.py::test_token_observer_nonblocking -x` | ❌ Wave 0 | ⬜ pending |
| BUG-04 fix | TBD | 1+ | BUG-04 | unit (asyncio, mock queue) | `pytest shuo/tests/test_bug_fixes.py::test_inactivity_watchdog_fires -x` | ❌ Wave 0 | ⬜ pending |
| BUG-04 fix | TBD | 1+ | BUG-04 | unit | `pytest shuo/tests/test_bug_fixes.py::test_watchdog_cancelled_on_stop -x` | ❌ Wave 0 | ⬜ pending |
| BUG-04 fix | TBD | 1+ | BUG-04 | unit | `pytest shuo/tests/test_bug_fixes.py::test_inactivity_timeout_env_var -x` | ❌ Wave 0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `shuo/tests/test_bug_fixes.py` — 8 test cases covering BUG-01 through BUG-04 (all start RED)
- [ ] pytest-asyncio `asyncio_mode = auto` configured (add to existing pytest config or create `shuo/pytest.ini`)

*Pre-existing 34 tests (test_update.py, test_isp.py, test_ivr_barge_in.py) are the regression guard.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real concurrent DTMF on live call does not corrupt state | BUG-01 | Requires live Twilio call + concurrent triggers | Place call, send DTMF from two sources simultaneously, verify `_dtmf_pending` stays consistent |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
