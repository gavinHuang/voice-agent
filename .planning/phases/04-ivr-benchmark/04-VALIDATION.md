---
phase: 4
slug: ivr-benchmark
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-21
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| **Config file** | `shuo/pyproject.toml` (pytest config inherited) |
| **Quick run command** | `cd shuo && python -m pytest tests/test_bench.py -x -q` |
| **Full suite command** | `cd shuo && python -m pytest tests/ -q` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd shuo && python -m pytest tests/test_bench.py -x -q`
- **After every plan wave:** Run `cd shuo && python -m pytest tests/ -q`
- **Before `/gsd:verify-work`:** Full suite must be green (57 existing + new bench tests)
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 4-W0-01 | 04-01 | 0 | BENCH-01–05 | setup | `cd shuo && python -m pytest tests/test_bench.py -x -q` | ❌ W0 | ⬜ pending |
| 4-01-01 | 04-01 | 1 | BENCH-01 | unit | `cd shuo && python -m pytest tests/test_bench.py::test_load_scenarios tests/test_bench.py::test_load_scenarios_invalid tests/test_bench.py::test_scenario_ivr_flow_default -x` | ❌ W0 | ⬜ pending |
| 4-01-02 | 04-01 | 1 | BENCH-03 | unit | `cd shuo && python -m pytest tests/test_bench.py::test_criterion_transcript_contains tests/test_bench.py::test_criterion_dtmf_sequence tests/test_bench.py::test_criterion_max_turns_exceeded tests/test_bench.py::test_all_criteria_and -x` | ❌ W0 | ⬜ pending |
| 4-02-01 | 04-02 | 2 | BENCH-02 | integration | `cd shuo && python -m pytest tests/test_bench.py::test_run_scenario_wires_localISP tests/test_bench.py::test_bench_no_api_keys -x` | ❌ W0 | ⬜ pending |
| 4-02-02 | 04-02 | 2 | BENCH-04 | unit | `cd shuo && python -m pytest tests/test_bench.py::test_metrics_report_fields tests/test_bench.py::test_cli_bench_integration -x` | ❌ W0 | ⬜ pending |
| 4-03-01 | 04-03 | 3 | BENCH-05 | e2e | `cd shuo && python -m pytest tests/test_bench.py::test_sample_scenarios_valid tests/test_bench.py::test_sample_scenarios_pass -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `shuo/tests/test_bench.py` — full test file covering BENCH-01 through BENCH-05 (all tests listed above)
- [ ] `shuo/shuo/bench.py` — benchmark runner module skeleton (imports must resolve before tests can collect)
- [ ] `scenarios/example_ivr.yaml` — 3 sample scenarios YAML file (needed by BENCH-05 tests)

*If none: "Existing infrastructure covers all phase requirements."*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Metrics printed to terminal match expected format | BENCH-04 | Visual formatting check | Run `voice-agent bench --dataset scenarios/example_ivr.yaml` and verify table output looks correct |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
