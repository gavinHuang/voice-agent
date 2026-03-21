---
phase: 6
slug: agent-framework-migration
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-22
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| **Config file** | `shuo/pyproject.toml` (pytest config inherited) |
| **Quick run command** | `cd shuo && python -m pytest tests/test_agent.py -x -q` |
| **Full suite command** | `cd shuo && python -m pytest tests/ -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd shuo && python -m pytest tests/test_agent.py -x -q`
- **After every plan wave:** Run `cd shuo && python -m pytest tests/ -q`
- **Before `/gsd:verify-work`:** Full suite must be green (105 existing + new agent tests)
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 6-W0-01 | 06-01 | 0 | AGENT-01–05 | setup | `cd shuo && python -m pytest tests/test_agent.py -x -q` | ❌ W0 | ⬜ pending |
| 6-01-01 | 06-01 | 1 | AGENT-01 | unit | `cd shuo && python -m pytest tests/test_agent.py::test_llm_service_streams_text_tokens tests/test_agent.py::test_llm_service_press_dtmf_tool tests/test_agent.py::test_llm_service_signal_hangup_tool -x` | ❌ W0 | ⬜ pending |
| 6-01-02 | 06-01 | 1 | AGENT-02 AGENT-03 | unit | `cd shuo && python -m pytest tests/test_agent.py::test_marker_scanner_deleted tests/test_agent.py::test_agent_no_marker_fields -x` | ❌ W0 | ⬜ pending |
| 6-02-01 | 06-02 | 2 | AGENT-04 | regression | `cd shuo && python -m pytest tests/ -q` | ✅ | ⬜ pending |
| 6-02-02 | 06-02 | 2 | AGENT-05 | unit | `cd shuo && python -m pytest tests/test_agent.py::test_llm_model_groq_prefix tests/test_agent.py::test_llm_model_openai_prefix -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `shuo/tests/test_agent.py` — full test file covering AGENT-01 through AGENT-05 (all tests listed above)

*Note: `shuo/shuo/agent.py` and `shuo/shuo/services/llm.py` already exist — Wave 0 only needs the test scaffold, not module skeletons.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Groq Llama 3.3 correctly invokes tool calls (not literal function syntax) | AGENT-01 | Model behavior depends on live Groq API; unit tests mock the model | Run `voice-agent local-call` with a goal requiring DTMF navigation and verify digit is sent; check trace file for `DTMFToneEvent` |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
