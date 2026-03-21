---
phase: 03-cli
plan: 01
subsystem: cli
tags: [click, pyyaml, pyproject, uvicorn, testing, mocking]

# Dependency graph
requires:
  - phase: 02-bug-fixes
    provides: stable server runtime (TwilioISP refactor, inactivity watchdog)
  - phase: 01-isp-abstraction
    provides: ISP protocol, LocalISP, conversation loop

provides:
  - voice-agent CLI entry point via pyproject.toml
  - Click group with shared YAML config loading and auto-detect from cwd
  - serve subcommand: starts uvicorn server with port/drain-timeout options and SIGTERM handler
  - call subcommand: initiates outbound call with --goal/--identity options
  - bench subcommand: stub output for Phase 4
  - Test suite for all CLI behavior with deferred-import mock strategy

affects:
  - 04-benchmark (bench subcommand stub will be filled in)
  - 06-agent-framework (CLI may need --model flag)

# Tech tracking
tech-stack:
  added: [click>=8.0.0, pyyaml>=6.0.0]
  patterns:
    - Click group with pass_context and ctx.obj for shared config state
    - Deferred imports inside Click command functions to avoid circular imports and module-level side effects
    - _ServerModuleContext test helper injects fake shuo.server + uvicorn into sys.modules
    - patch load_dotenv in env-check tests to prevent .env from repopulating cleared env vars

key-files:
  created:
    - shuo/pyproject.toml
    - shuo/shuo/cli.py
    - shuo/tests/test_cli.py
  modified: []

key-decisions:
  - "Deferred imports inside Click commands: shuo.server and uvicorn imported inside function body to avoid dashboard import error at CLI startup"
  - "Identity prepended to goal string and written to CALL_GOAL env var: server reads CALL_GOAL when processing the call"
  - "SIGTERM handler uses mutable list _uvicorn_server[0] pattern: closure mutation without global"
  - "_ServerModuleContext test pattern: inject fake modules into sys.modules for dashboard-dependent imports"
  - "load_dotenv mock required in env-check tests: real .env file repopulates cleared env vars otherwise"

patterns-established:
  - "Deferred-import pattern: heavy/side-effect imports (shuo.server, uvicorn, twilio_client) inside Click command functions"
  - "_ServerModuleContext: reusable context manager for injecting fake modules in CLI tests"
  - "YAML config merge: effective_value = flag if flag is not None else cfg.get(key, fallback)"

requirements-completed: [CLI-01, CLI-02, CLI-04, CLI-05]

# Metrics
duration: 13min
completed: 2026-03-21
---

# Phase 3 Plan 1: CLI Foundation Summary

**Click-based voice-agent CLI with serve/call/bench subcommands, YAML config loading with auto-detect and flag override, packaged via pyproject.toml entry point**

## Performance

- **Duration:** 13 min
- **Started:** 2026-03-21T11:17:06Z
- **Completed:** 2026-03-21T11:29:58Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- `pyproject.toml` with `voice-agent = "shuo.cli:main"` entry point and full dependency list
- `cli.py` Click group with YAML config auto-detect from cwd, `--config` override, `load_dotenv` + `setup_logging` in group callback
- `serve` subcommand: port/drain-timeout options, env var check for all 7 required vars, SIGTERM graceful drain handler, uvicorn server in daemon thread
- `call` subcommand: required `phone` argument, `--goal`/`--identity` options, identity prepended to goal, `CALL_GOAL` env var set before server start
- `bench` subcommand: `--dataset` option, stub output referencing Phase 4
- 11 CLI tests all passing; full suite of 45 tests green (no regressions)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create pyproject.toml and cli.py** - `c598f42` (feat)
2. **Task 2: Tests for CLI behavior** - `c6349db` (test)

## Files Created/Modified
- `shuo/pyproject.toml` - Package metadata with voice-agent entry point and all dependencies
- `shuo/shuo/cli.py` - Click group with serve, call, bench subcommands and YAML config loading
- `shuo/tests/test_cli.py` - 11 tests covering all CLI subcommands, config loading, flag overrides

## Decisions Made
- **Deferred imports**: `shuo.server`, `uvicorn`, and `twilio_client` are imported inside Click command function bodies rather than at module top level. `shuo.server` imports `dashboard` (a repo-root package), which causes `ImportError` at CLI startup when `dashboard` is not on `sys.path`. Deferring keeps the CLI importable without the full server stack.
- **Identity to CALL_GOAL env var**: The server reads `CALL_GOAL` from environment when processing a call. Prepending the identity string to goal and writing it to `CALL_GOAL` before `make_outbound_call` is the simplest integration without changing server internals.
- **`_ServerModuleContext` test helper**: Created a context manager that injects `shuo.server` and `uvicorn` fake module objects into `sys.modules`. This was necessary because the deferred `from shuo.server import app` would otherwise fail in test environment due to missing `dashboard` package.
- **`load_dotenv` mock in env-check test**: The `.env` file in the project has all 7 required vars set. `load_dotenv()` is called in the CLI group callback before `_check_env_vars`. Patching `load_dotenv` prevents the .env file from repopulating env vars when testing missing-env-var behavior.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Tests required _ServerModuleContext pattern to work in test environment**
- **Found during:** Task 2 (TDD RED phase)
- **Issue:** Plan's proposed mock approach (`@patch("shuo.cli.threading.Thread")` alone) failed because `from shuo.server import app` inside the serve function raised `ImportError: No module named 'dashboard'`
- **Fix:** Created `_ServerModuleContext` class in test file that pre-injects fake `shuo.server` and `uvicorn` module objects into `sys.modules` before invoking the CLI
- **Files modified:** shuo/tests/test_cli.py
- **Verification:** All 11 CLI tests pass; 45 total green
- **Committed in:** c6349db (Task 2 commit)

**2. [Rule 1 - Bug] load_dotenv mock required for env-check test**
- **Found during:** Task 2 (test debugging)
- **Issue:** `test_serve_env_check_fails` hung indefinitely because `load_dotenv()` in the CLI group callback repopulated env vars from `.env` file even with `patch.dict(os.environ, {}, clear=True)`, causing env check to pass and serve to block on `time.sleep(2)`
- **Fix:** Added `patch("shuo.cli.load_dotenv")` to prevent .env loading in that test
- **Files modified:** shuo/tests/test_cli.py
- **Verification:** Test passes in 0.02s
- **Committed in:** c6349db (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (both Rule 1 — test correctness bugs)
**Impact on plan:** Both fixes were necessary for tests to work in this codebase's environment. No scope creep.

## Issues Encountered
- `dashboard` package (repo-root sibling) is imported by `shuo/server.py` at module level; this is a pre-existing coupling that makes server un-importable in test environments without sys.path manipulation. Worked around via deferred imports in cli.py + fake module injection in tests. Not fixed (out of scope for CLI phase).

## Next Phase Readiness
- `voice-agent serve/call/bench` commands are installable and functional
- Phase 4 (benchmark) can implement the bench runner; the stub and `--dataset` option are in place
- Phase 6 (agent framework) may need to add `--model` flag to serve/call subcommands

---
*Phase: 03-cli*
*Completed: 2026-03-21*
