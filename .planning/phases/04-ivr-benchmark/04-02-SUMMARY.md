---
phase: 04-ivr-benchmark
plan: "02"
subsystem: testing
tags: [benchmark, ivr, httpx, asyncio, twiml, click, fastapi]

requires:
  - phase: 04-ivr-benchmark plan 01
    provides: bench.py data model (SuccessCriteria, ScenarioConfig, CriteriaResult, ScenarioResult, load_scenarios, evaluate_criteria)
  - phase: 03-cli
    provides: cli.py bench stub and _run_local_call asyncio.wait pattern
  - phase: 01-isp-abstraction
    provides: LocalISP with _inject hook, send_dtmf interface
provides:
  - IVRDriver that walks TwiML state machine via HTTP loopback (POST /twiml, /ivr/step, /ivr/gather)
  - BenchISP (LocalISP subclass) capturing DTMF into dtmf_log and _dtmf_queue
  - run_scenario orchestrating agent+IVR pair without Deepgram/Groq/ElevenLabs API keys
  - run_benchmark sequencing all scenarios with ephemeral IVR server startup
  - print_metrics_report printing terminal table with PASS/FAIL, turns, DTMF%, latency
  - bench CLI command wired to run_benchmark with --dataset and --output flags
affects: [05-supervisor, end-to-end testing]

tech-stack:
  added: [httpx (async HTTP client for IVR HTTP calls), xml.etree.ElementTree (TwiML parsing)]
  patterns:
    - "Deferred import of run_conversation inside run_scenario body (Phase 3 convention)"
    - "asyncio.wait(FIRST_COMPLETED) for agent+IVR task pair — same as _run_local_call"
    - "Poll _inject is not None before driving IVR to ensure conversation loop is ready"
    - "_BenchFluxPool and _BenchTTSPool no-op implementations eliminate API key requirement"
    - "_find_free_port + daemon uvicorn thread for ephemeral test server lifecycle"

key-files:
  created: []
  modified:
    - shuo/shuo/bench.py
    - shuo/shuo/cli.py
    - shuo/tests/test_bench.py
    - shuo/tests/test_cli.py

key-decisions:
  - "BenchISP subclasses LocalISP and overrides send_dtmf only — inherits all ISP behavior"
  - "IVRDriver polls _dtmf_queue with asyncio.wait_for to get agent DTMF with per-step timeout"
  - "Top-level Redirect only (not inside Gather) treated as step navigation; Gather's action URL supplies gather_node_id"
  - "run_scenario polls bench_isp._inject up to 0.5s before launching IVRDriver to avoid race"
  - "No-op flux/tts pools eliminate all API key requirements for benchmark runs"
  - "Patch target for run_conversation tests is shuo.conversation.run_conversation (deferred import)"
  - "print_metrics_report uses dtmf_pass bool from CriteriaResult for DTMF% column"

patterns-established:
  - "Benchmark wiring test: patch shuo.conversation.run_conversation + mock IVRDriver"
  - "CLI bench tests: patch shuo.bench.run_benchmark (AsyncMock) and assert call_args"

requirements-completed: [BENCH-02, BENCH-04]

duration: 3min
completed: 2026-03-21
---

# Phase 4 Plan 02: Benchmark Runner Summary

**TwiML state-machine driver (IVRDriver + BenchISP) wired to run_scenario/run_benchmark with no-op flux/tts pools eliminating all API key requirements**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-21T12:33:28Z
- **Completed:** 2026-03-21T12:36:48Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- IVRDriver walks TwiML state machine via HTTP loopback, injecting `<Say>` text as `FluxEndOfTurnEvent` into agent and capturing DTMF from `BenchISP._dtmf_queue`
- run_scenario pairs BenchISP agent with IVRDriver using `asyncio.wait(FIRST_COMPLETED)`, no Deepgram/Groq/ElevenLabs API keys needed
- bench CLI command fully wired: `voice-agent bench --dataset file.yaml` runs run_benchmark and prints metrics report

## Task Commits

Each task was committed atomically:

1. **Task 1: Implement IVRDriver, BenchISP, run_scenario, run_benchmark, print_metrics_report** - `ecaa467` (feat)
2. **Task 2: Wire bench CLI command to run_benchmark** - `0e8698f` (feat)

**Plan metadata:** (docs commit follows)

## Files Created/Modified

- `shuo/shuo/bench.py` - Added BenchISP, IVRDriver, _extract_say_and_gather, _BenchFluxPool, _BenchTTSPool, run_scenario, run_benchmark, print_metrics_report, _find_free_port, _start_ivr_server, _wait_for_ivr_ready
- `shuo/shuo/cli.py` - Replaced bench stub with real implementation calling run_benchmark, added --output flag
- `shuo/tests/test_bench.py` - Added 9 new tests covering IVR driver parsing, BenchISP wiring, no-API-keys guarantee, metrics report output
- `shuo/tests/test_cli.py` - Updated 5 bench tests to mock run_benchmark and assert call_args; test_bench_no_dataset now asserts exit_code != 0

## Decisions Made

- BenchISP subclasses LocalISP and only overrides `send_dtmf` — inherits all ISP lifecycle behavior cleanly
- IVRDriver uses `asyncio.wait_for(_dtmf_queue.get(), timeout=per_step_timeout)` where per_step_timeout = max(timeout/2, 5.0)
- Top-level `<Redirect>` only treated as step navigation; `<Gather action=...>` supplies the gather node ID
- `run_scenario` polls `bench_isp._inject is not None` up to 0.5s before launching IVRDriver to avoid startup race with `run_conversation`
- `_BenchFluxPool` and `_BenchTTSPool` are in-module no-op stubs; no real Deepgram/Groq/ElevenLabs initialization
- Patch target for `run_conversation` in tests: `shuo.conversation.run_conversation` (not `shuo.bench.run_conversation`) because the import is deferred inside the function body

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed patch target for run_conversation in test_run_scenario_wires_localISP**
- **Found during:** Task 1 verification
- **Issue:** Plan specified `patch("shuo.bench.run_conversation")` but `run_conversation` is imported inside the function body — `shuo.bench` has no such attribute at module level
- **Fix:** Changed patch target to `shuo.conversation.run_conversation` which is where the actual function lives
- **Files modified:** shuo/tests/test_bench.py
- **Verification:** Test passes after fix
- **Committed in:** ecaa467 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug in test patch target)
**Impact on plan:** Minimal — one-line fix required by deferred import pattern established in Phase 3.

## Issues Encountered

None beyond the auto-fixed patch target issue above.

## Next Phase Readiness

- Benchmark runner complete: `voice-agent bench --dataset scenarios.yaml` works end-to-end
- BENCH-02 and BENCH-04 requirements satisfied
- Pre-existing `test_dtmf_pending_sequential` and `test_dtmf_lock_concurrent` failures in test_bug_fixes.py (ModuleNotFoundError: dashboard) are unrelated to this plan

---
*Phase: 04-ivr-benchmark*
*Completed: 2026-03-21*
