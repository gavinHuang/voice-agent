---
phase: 04-ivr-benchmark
plan: 03
subsystem: testing
tags: [pytest, asyncio, ivr, benchmark, e2e, httpx, uvicorn, click]

# Dependency graph
requires:
  - phase: 04-ivr-benchmark/04-01
    provides: bench.py data models, run_scenario, BenchISP, IVRDriver, _start_ivr_server helpers
  - phase: 04-ivr-benchmark/04-02
    provides: IVRDriver.drive, run_benchmark, print_metrics_report
provides:
  - E2E tests proving the full benchmark pipeline: YAML load -> IVR server -> IVRDriver -> fake agent -> criteria evaluation
  - test_sample_scenarios_valid: schema check for example_ivr.yaml
  - test_sample_scenarios_pass: all 3 scenarios pass against real IVR mock server
  - test_cli_bench_integration: CLI bench command produces metrics output
affects: [future benchmark extensions, CI/CD pipeline, phase 05+]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-scenario fake run_conversation factory pattern: each scenario gets a dedicated async mock"
    - "patch('shuo.conversation.run_conversation') not 'shuo.bench.run_conversation' — deferred import requires patching the source module"
    - "conftest.py adds project root to sys.path so ivr package is importable when _start_ivr_server is called"
    - "Fake agent breaks out of event loop after reaching goal (avoids per-step timeout delays in tests)"

key-files:
  created:
    - shuo/tests/conftest.py
  modified:
    - shuo/tests/test_bench.py

key-decisions:
  - "Patch shuo.conversation.run_conversation (not shuo.bench.run_conversation): run_scenario uses deferred import inside function body, so patching the source module is required"
  - "conftest.py adds project root to sys.path: cleanest solution for ivr package importability without changing pyproject.toml"
  - "Per-scenario fake agents break out cleanly after reaching goal: avoids 5s per-step-timeout delays and keeps test runtime under 6 seconds"
  - "Single IVR server instance shared across all 3 scenarios in test_sample_scenarios_pass: server is stateless so safe to reuse"

patterns-established:
  - "E2E benchmark tests: start real IVR server, mock run_conversation with DTMF-simulating agent, assert criteria pass"
  - "Fake conversation sets isp._inject immediately on entry — required before IVRDriver can start driving"

requirements-completed: [BENCH-05]

# Metrics
duration: 5min
completed: 2026-03-21
---

# Phase 4 Plan 3: IVR Benchmark E2E Tests Summary

**E2E tests proving YAML -> IVR server -> IVRDriver -> fake agent -> criteria evaluation pipeline for all 3 sample scenarios, with per-scenario DTMF-simulating mocks replacing run_conversation**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-21T12:39:00Z
- **Completed:** 2026-03-21T12:44:40Z
- **Tasks:** 1
- **Files modified:** 2 (test_bench.py modified, conftest.py created)

## Accomplishments
- `test_sample_scenarios_valid`: confirms `example_ivr.yaml` loads exactly 3 `ScenarioConfig` objects with required fields
- `test_sample_scenarios_pass`: starts a real IVR server, mocks `run_conversation` with DTMF-simulating fake agents, asserts all 3 scenarios pass criteria (navigate-to-sales dtmf_log=["1"], navigate-to-tech-support dtmf_log=["2","1"], timeout-no-input max_turns<=20)
- `test_cli_bench_integration`: invokes the `bench` CLI command via `CliRunner`, mocks `run_benchmark`, asserts exit code 0 and all scenario IDs appear in output
- Full test suite: 80 tests passing (18 existing bench + 3 new + all other tests)

## Task Commits

Each task was committed atomically:

1. **Task 1: Add e2e tests for sample scenarios against IVR mock** - `205b085` (feat)

**Plan metadata:** (pending final docs commit)

## Files Created/Modified
- `shuo/tests/test_bench.py` - Added 3 new test functions (test_sample_scenarios_valid, test_sample_scenarios_pass, test_cli_bench_integration) plus imports for _find_free_port, _start_ivr_server, _wait_for_ivr_ready, run_scenario, run_benchmark
- `shuo/tests/conftest.py` - New file: adds project root to sys.path so `ivr` package is importable during e2e tests

## Decisions Made
- Patched `shuo.conversation.run_conversation` (not `shuo.bench.run_conversation`): `run_scenario` imports `run_conversation` inside the function body via a deferred import, so patching the source module is the correct approach
- Added `conftest.py` to inject project root into `sys.path`: the `ivr` package lives at the project root level, outside the `shuo` package; this is the minimal change that makes `_start_ivr_server` work in tests without modifying `pyproject.toml`
- Per-scenario fake agents break out of their event loop after confirming goal reached: avoids 5-second per-step timeout delays and keeps test runtime under 6 seconds total
- Shared single IVR server instance across all 3 scenarios: the IVR server is stateless between requests so reuse is safe

## Deviations from Plan

None - plan executed exactly as written. The implementation approach matched the recommended "real IVR server in daemon thread" pattern. The only discovery was that `run_conversation` must be patched at `shuo.conversation` (not `shuo.bench`) due to deferred imports — this is correct patching behavior, not a bug.

## Issues Encountered
- First patch attempt targeted `shuo.bench.run_conversation` which raised `AttributeError` because `run_scenario` uses a deferred `from shuo.conversation import run_conversation` inside the function. Resolved by patching `shuo.conversation.run_conversation` instead.
- CLI test initially produced empty output because the mocked `run_benchmark` didn't call `print_metrics_report`. Resolved by having the mock call `print_metrics_report(pre_built_results)` before returning.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- BENCH-05 complete: full e2e validation of the benchmark pipeline is proven
- Phase 4 (ivr-benchmark) is now fully implemented with all 5 requirements satisfied
- 80 tests passing, suite is clean

---
*Phase: 04-ivr-benchmark*
*Completed: 2026-03-21*
