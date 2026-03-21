---
phase: 3
slug: cli
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-21
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| **Config file** | none — existing `shuo/tests/` directory |
| **Quick run command** | `cd shuo && python -m pytest tests/test_cli.py -q` |
| **Full suite command** | `cd shuo && python -m pytest tests/ --ignore=tests/test_bug_fixes.py -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd shuo && python -m pytest tests/test_cli.py -q`
- **After every plan wave:** Run `cd shuo && python -m pytest tests/ --ignore=tests/test_bug_fixes.py -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 3-W0-01 | 03-01 | 0 | CLI-01–05 | setup | `cd shuo && python -m pytest tests/test_cli.py -q` | ❌ W0 | ⬜ pending |
| 3-01-01 | 03-01 | 1 | CLI-01 | unit | `cd shuo && python -m pytest tests/test_cli.py::test_serve_starts_server -x` | ❌ W0 | ⬜ pending |
| 3-01-02 | 03-01 | 1 | CLI-02 | unit | `cd shuo && python -m pytest tests/test_cli.py::test_call_invokes_outbound -x` | ❌ W0 | ⬜ pending |
| 3-01-03 | 03-01 | 1 | CLI-03 | integration | `cd shuo && python -m pytest tests/test_cli.py::test_local_call_runs -x` | ❌ W0 | ⬜ pending |
| 3-01-04 | 03-01 | 1 | CLI-04 | unit | `cd shuo && python -m pytest tests/test_cli.py::test_bench_stub -x` | ❌ W0 | ⬜ pending |
| 3-01-05 | 03-01 | 1 | CLI-05 | unit | `cd shuo && python -m pytest tests/test_cli.py::test_config_flag_override -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `shuo/tests/test_cli.py` — stub test file with test functions for CLI-01 through CLI-05 using `click.testing.CliRunner`
- [ ] `shuo/pyproject.toml` — required before any CLI test can import `shuo.cli`; Wave 0 task creates this alongside the test stub

*Note: `shuo/cli.py` is the Wave 1 implementation target. Wave 0 creates the test file and pyproject.toml so the entry point is importable during tests.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `voice-agent` command available in PATH after install | CLI-01 | Requires actual `pip install -e .` and shell PATH check | Run `pip install -e . && which voice-agent` from `shuo/` directory; confirm non-empty output |
| `voice-agent call` initiates a real Twilio call | CLI-02 | Requires live Twilio credentials and network | Run `voice-agent call +1234567890 --goal "test"` with real credentials; check Twilio console for call SID |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
