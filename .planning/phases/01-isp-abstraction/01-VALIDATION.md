---
phase: 1
slug: isp-abstraction
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-21
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x + pytest-asyncio |
| **Config file** | none — pytest defaults |
| **Quick run command** | `python3 -m pytest shuo/tests/test_update.py -q --tb=short` |
| **Full suite command** | `python3 -m pytest shuo/tests/ -q --tb=short` |
| **Estimated runtime** | ~3 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest shuo/tests/test_update.py -q --tb=short`
- **After every plan wave:** Run `python3 -m pytest shuo/tests/ -q --tb=short`
- **Before `/gsd:verify-work`:** Full suite must be green (26 original + new ISP tests)
- **Max feedback latency:** ~5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 01-?-01 | TBD | 0 | ISP-01, ISP-02, ISP-03 | unit | `pytest shuo/tests/test_isp.py -x` | ❌ Wave 0 | ⬜ pending |
| 01-?-02 | TBD | 0 | ISP-04 | integration | `pytest shuo/tests/test_ivr_barge_in.py -x` | ✅ update needed | ⬜ pending |
| 01-?-03 | TBD | 1+ | ISP-01 | unit | `pytest shuo/tests/test_isp.py::test_protocol_shape -x` | ❌ Wave 0 | ⬜ pending |
| 01-?-04 | TBD | 1+ | ISP-02 | unit | `pytest shuo/tests/test_isp.py::test_twilio_isp_send_audio -x` | ❌ Wave 0 | ⬜ pending |
| 01-?-05 | TBD | 1+ | ISP-03 | unit | `pytest shuo/tests/test_isp.py::test_local_isp_audio_routing -x` | ❌ Wave 0 | ⬜ pending |
| 01-?-06 | TBD | 1+ | ISP-03 | unit | `pytest shuo/tests/test_isp.py::test_local_isp_dtmf -x` | ❌ Wave 0 | ⬜ pending |
| 01-?-07 | TBD | 1+ | ISP-03 | unit | `pytest shuo/tests/test_isp.py::test_local_isp_hangup -x` | ❌ Wave 0 | ⬜ pending |
| 01-?-08 | TBD | 1+ | ISP-05 | unit | `pytest shuo/tests/test_update.py -q` | ✅ | ⬜ pending |
| 01-?-09 | TBD | 1+ | ISP-04, ISP-05 | integration | `pytest shuo/tests/test_ivr_barge_in.py -q` | ✅ update needed | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `shuo/tests/test_isp.py` — new test file covering ISP Protocol shape (ISP-01), TwilioISP protocol conformance (ISP-02), LocalISP audio routing/DTMF/hangup (ISP-03)
- [ ] `shuo/tests/test_ivr_barge_in.py` — update 2 existing integration tests to use new `run_conversation(isp, ...)` signature instead of `run_conversation_over_twilio(websocket, ...)`

*No new framework install needed — pytest-asyncio already available.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real Twilio call still connects end-to-end | ISP-02 | Requires live Twilio credentials + ngrok | Start server, dial test number, verify audio flows |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
