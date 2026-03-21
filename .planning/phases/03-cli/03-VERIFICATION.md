---
phase: 03-cli
verified: 2026-03-21T18:15:00Z
status: passed
score: 11/11 must-haves verified
re_verification: false
---

# Phase 3: CLI Verification Report

**Phase Goal:** A `voice-agent` command provides a single entry point for all platform capabilities, with YAML config files and flag overrides
**Verified:** 2026-03-21T18:15:00Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| #  | Truth | Status | Evidence |
|----|-------|--------|----------|
| 1  | `voice-agent serve` starts a uvicorn server on the configured port | VERIFIED | `cli.py:144` — daemon `threading.Thread(target=_start_server)` calls `uvicorn.Config(app, host="0.0.0.0", port=effective_port)` + `uvicorn.Server.run()`; `test_serve_starts_server` asserts `Thread` called with `daemon=True` |
| 2  | `voice-agent call <phone>` invokes `make_outbound_call` with the phone number | VERIFIED | `cli.py:192` — `make_outbound_call(phone)` called after server start; `test_call_invokes_outbound` asserts `fake_make_call.assert_called_once_with("+15551234567")` |
| 3  | `voice-agent bench --dataset X` prints stub message with the dataset path | VERIFIED | `cli.py:209-210` — prints `f"bench: dataset={effective_dataset!r}"` and `"Benchmark runner not yet implemented (Phase 4)."`; confirmed by live run |
| 4  | YAML config values are used when CLI flags are absent | VERIFIED | `cli.py:208` — `effective_dataset = dataset if dataset is not None else cfg.get("dataset")`; `test_config_file_loaded` and `test_config_auto_detect` both pass |
| 5  | CLI flags override YAML config values | VERIFIED | Same merge expression; `test_flag_overrides_config` asserts flag value present and config value absent |
| 6  | `voice-agent.yaml` in cwd is auto-loaded when no `--config` is specified | VERIFIED | `cli.py:52` — `auto_path = os.path.join(os.getcwd(), "voice-agent.yaml")`; `test_config_auto_detect` passes using `runner.isolated_filesystem()` |
| 7  | `voice-agent local-call` runs two LLM agents concurrently using `LocalISP` | VERIFIED | `cli.py:237` — `LocalISP.pair(isp_caller, isp_callee)` then two `asyncio.create_task(run_conversation(...))` tasks; `test_local_call_runs` asserts `mock_run_conv.call_count == 2` and `mock_isp_cls.pair.called` |
| 8  | Live interleaved transcript prints to stdout with `[CALLER]` and `[CALLEE]` labels | VERIFIED | `cli.py:213-220` — `_make_observer(label)` function emits `f"[{label}]: {event['text']}"` on transcript events; `_make_observer("CALLER")` and `_make_observer("CALLEE")` called in `_run_local_call` |
| 9  | Call ends when either side hangs up | VERIFIED | `cli.py:257` — `asyncio.wait([task_caller, task_callee], return_when=asyncio.FIRST_COMPLETED)` then cancels pending tasks |
| 10 | `local-call` accepts `--caller-goal`, `--caller-identity`, `--callee-goal`, `--callee-identity` flags | VERIFIED | `cli.py:289-292` — all four options declared; `test_local_call_help` asserts all four present in `--help` output |
| 11 | `local-call` reads caller/callee config from `local_call` section of YAML config | VERIFIED | `cli.py:302-304` — `cfg = ctx.obj["config"].get("local_call", {})`; `test_local_call_config_merge` and `test_local_call_flag_overrides_config` both pass |

**Score:** 11/11 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `shuo/pyproject.toml` | Package metadata and `voice-agent` entry point | VERIFIED | Contains `voice-agent = "shuo.cli:main"` in `[project.scripts]`; `click>=8.0.0` and `pyyaml>=6.0.0` in dependencies; `[build-system]` with setuptools present |
| `shuo/shuo/cli.py` | Click CLI group with serve, call, local-call, bench subcommands and config loading | VERIFIED | 323 lines; `@click.group()`, `def serve`, `def call_cmd` (`name="call"`), `def bench`, `def local_call` (`name="local-call"`), `def _load_config`, `yaml.safe_load`, `def main()`, `make_outbound_call` import, `uvicorn.Config`/`uvicorn.Server` usage |
| `shuo/tests/test_cli.py` | Tests for all subcommands, config loading, and flag override behavior | VERIFIED | 379 lines; 17 tests covering bench stub, YAML config loading (4 tests), serve (3 tests), call (2 tests), local-call (6 tests); all 17 pass |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `shuo/pyproject.toml` | `shuo/shuo/cli.py` | entry point declaration | VERIFIED | `voice-agent = "shuo.cli:main"` at line 24 matches `shuo.cli:main` |
| `shuo/shuo/cli.py` | `shuo/shuo/services/twilio_client.py` | deferred import of `make_outbound_call` | VERIFIED | `cli.py:165` — `from shuo.services.twilio_client import make_outbound_call` inside `call_cmd`; called at line 192 |
| `shuo/shuo/cli.py` | `shuo/shuo/services/local_isp.py` | imports `LocalISP` and calls `pair()` | VERIFIED | `cli.py:232-237` — `from shuo.services.local_isp import LocalISP` inside `_run_local_call`; `LocalISP.pair(isp_caller, isp_callee)` called |
| `shuo/shuo/cli.py` | `shuo/shuo/conversation.py` | calls `run_conversation()` for each agent side | VERIFIED | `cli.py:233` — `from shuo.conversation import run_conversation` inside `_run_local_call`; called twice at lines 242-254 |
| `shuo/shuo/cli.py` | stdout | observer callback prints `[CALLER]`/`[CALLEE]` transcript lines | VERIFIED | `_make_observer` at lines 213-220; `f"[{label}]: {event['text']}"` emitted to stdout via `click.echo` |

Note: The key link from `cli.py` to `main.py` via `start_server` documented in the plan was intentionally avoided — the executor inlined uvicorn startup to prevent importing `main.py` (which has module-level side effects). The actual wiring (cli → uvicorn.Server → shuo.server app) was verified.

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| CLI-01 | 03-01 | `voice-agent serve` starts the backend server | SATISFIED | `serve` subcommand at `cli.py:85-153`; starts uvicorn with port/drain-timeout config; env var check for all 7 vars |
| CLI-02 | 03-01 | `voice-agent call <phone>` places outbound call with `--goal` and `--identity` | SATISFIED | `call_cmd` subcommand at `cli.py:156-199`; required `phone` arg, `--goal`/`--identity` options, `make_outbound_call` invoked |
| CLI-03 | 03-02 | `voice-agent local-call` runs two agents using `LocalISP` | SATISFIED | `local_call` subcommand at `cli.py:288-317`; `LocalISP.pair`, two `run_conversation` tasks, `asyncio.FIRST_COMPLETED` termination |
| CLI-04 | 03-01 | `voice-agent bench` runs IVR benchmark scenarios from a YAML file (stub) | SATISFIED | `bench` subcommand at `cli.py:202-210`; `--dataset` flag wired to YAML config; stub output confirms Phase 4 intent |
| CLI-05 | 03-01 | All CLI commands accept YAML config files; flags are overrides | SATISFIED | `_load_config` at `cli.py:32-62`; auto-detect `voice-agent.yaml` from cwd; `--config` explicit path; per-command `cfg.get(key, fallback)` merge pattern used in all four subcommands |

**No orphaned requirements.** REQUIREMENTS.md traceability table maps CLI-01 through CLI-05 exclusively to Phase 3. All five are claimed in the plans and all five are implemented.

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `shuo/shuo/cli.py` | 209 | `bench` prints stub + "not yet implemented" | Info | Intentional placeholder for Phase 4; bench is a stub by design (CLI-04 says "stub") |

No blockers or warnings found. The bench stub is expected and documented in CLI-04.

---

### Human Verification Required

None. All CLI behaviors are fully verifiable programmatically:

- Entry point wiring verified via `pyproject.toml` content
- Subcommand behavior verified via `CliRunner` tests that all pass
- Config loading/override logic covered by isolated filesystem tests
- Concurrent conversation and `FIRST_COMPLETED` termination tested with `AsyncMock`

---

### Gaps Summary

No gaps. All 11 observable truths verified, all 3 artifacts substantive and wired, all 5 key links confirmed, all 5 requirement IDs satisfied. Full test suite (51 tests) passes with no regressions.

---

_Verified: 2026-03-21T18:15:00Z_
_Verifier: Claude (gsd-verifier)_
