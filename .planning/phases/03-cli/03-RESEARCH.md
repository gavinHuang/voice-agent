# Phase 3: CLI - Research

**Researched:** 2026-03-21
**Domain:** Python CLI tooling, pyproject.toml packaging, YAML config, asyncio entry points
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Config file holds **behavioral settings only** — credentials stay in env vars
- Config uses **per-command sections** matching subcommand names (`serve`, `call`, `local_call`, `bench`)
- Default config location: `voice-agent.yaml` in the **current working directory** (auto-loaded if present; `--config` flag overrides)
- Flag values override config file values (flags win)
- Both sides of `local-call` are **LLM agents** — caller agent vs callee agent (no IVR mock)
- Each agent has its own `goal` and `identity` via `local_call.caller` / `local_call.callee` sections (or `--caller-*` / `--callee-*` flags)
- Terminal output: **live interleaved transcript** with speaker labels (`[CALLER]` / `[CALLEE]`), printed in real-time
- Call ends when hangup detected; summary printed on completion
- Package entry point via **`pyproject.toml`** with `[project.scripts]`: `voice-agent = "shuo.cli:main"`
- Installed via `pip install -e .`
- CLI module lives at **`shuo/cli.py`** (top level of the shuo directory, sibling to `main.py`)
- `main.py` can remain for backwards compatibility or be deprecated

### Claude's Discretion
- CLI framework choice (Click, argparse, Typer — all compatible with Python 3.9+)
- pyproject.toml package metadata (name, version, dependencies)
- Exact flag names and short flags for each subcommand
- How `voice-agent bench` wires into Phase 4 benchmark runner (stub or thin delegator acceptable)
- How `local-call` transcript output is formatted beyond speaker labels (timestamps, colors, etc.)

### Deferred Ideas (OUT OF SCOPE)
- None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| CLI-01 | `voice-agent serve` starts the backend server (equivalent to current `main.py`) | `start_server()`, SIGTERM drain, `check_environment()` in `main.py` are directly reusable; uvicorn daemon-thread pattern confirmed |
| CLI-02 | `voice-agent call <phone>` places an outbound call with `--goal` and `--identity` flags | `make_outbound_call(to_number)` in `twilio_client.py` is the direct call site; server must start first (matches current `main.py` behavior) |
| CLI-03 | `voice-agent local-call` runs a call between two agents using `LocalISP` (no Twilio) | `LocalISP.pair()` confirmed; `run_conversation()` with `observer` callback prints live transcript; `asyncio.gather()` runs both sides concurrently |
| CLI-04 | `voice-agent bench` runs IVR benchmark scenarios from a YAML file | Phase 3 delivers CLI stub only; Phase 4 owns BENCH-01–05 runner logic |
| CLI-05 | All CLI commands accept YAML config files; flags are overrides | PyYAML 6.0.3 already installed; `default=None` on Click options enables clean flag-wins detection |
</phase_requirements>

---

## Summary

Phase 3 adds a `voice-agent` CLI entry point that unifies all existing platform capabilities behind a consistent interface with YAML config and flag-override support. The codebase already contains working `start_server()`, `make_outbound_call()`, `LocalISP.pair()`, and `run_conversation()` — this phase is composition, not new logic.

Click 8.3.1 is already installed as a transitive uvicorn dependency, making it the zero-cost choice. PyYAML 6.0.3 is also already present. No new packages need to be installed. The entire CLI implementation is: create `shuo/cli.py` with a Click group and four subcommands, create `shuo/pyproject.toml` with the entry point declaration, and wire each subcommand to existing functions.

The `local-call` subcommand is the most complex: it must run two `run_conversation()` coroutines concurrently within a single `asyncio.run()` call, wire observer callbacks for live transcript printing to stdout, and terminate cleanly when either side hangs up. The `LocalISP` pair/start ordering constraint is a known footgun.

**Primary recommendation:** Use Click 8.3.1 (already installed). `pyproject.toml` goes in `shuo/` (co-located with `main.py`). Keep config-merge logic as a small `_load_config()` / `_merge()` pair shared by all subcommands.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| click | 8.3.1 | CLI framework — groups, commands, options, arguments | Already installed (uvicorn dep); group/command model maps perfectly to subcommand structure |
| pyyaml | 6.0.3 | YAML config file loading | Already installed; `yaml.safe_load()` is the standard safe parse |
| uvicorn | 0.41.0 | ASGI server used by `serve` subcommand | Already used in `main.py`; `uvicorn.Config` + `uvicorn.Server` pattern established |
| python-dotenv | >=1.0.0 | Env var loading via `load_dotenv()` | Already in requirements.txt; used in `main.py`; CLI must preserve this |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| asyncio (stdlib) | Python 3.x | Run two concurrent `run_conversation()` tasks in `local-call` | `asyncio.run()` + `asyncio.gather()` or `asyncio.wait()` |
| setuptools | >=68 | pyproject.toml build backend | Needed for `pip install -e .` |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Click | Typer | Typer is not installed; adds a dependency for no functional gain over Click |
| Click | argparse | argparse has no native subcommand group concept as clean as `@cli.command()` |
| pyyaml | tomllib (stdlib 3.11+) | YAML is the decided config format; TOML would change the file format |

**Installation — no new packages needed:**
```bash
# Click and pyyaml are already present. Only pyproject.toml needs to be created.
pip install -e .   # run from shuo/ directory after creating pyproject.toml
```

**Version verification (confirmed 2026-03-21 via `importlib.metadata`):**
- `click` 8.3.1
- `pyyaml` 6.0.3
- `uvicorn` 0.41.0
- Python runtime: 3.14.2

---

## Architecture Patterns

### Recommended Project Structure
```
shuo/
├── pyproject.toml          # NEW — [project.scripts] entry point
├── main.py                 # KEEP for backwards compatibility
├── requirements.txt        # Unchanged
└── shuo/
    ├── cli.py              # NEW — Click group + four subcommand functions
    ├── conversation.py     # Unchanged — run_conversation() used by local-call
    ├── server.py           # Unchanged — FastAPI app used by serve
    └── services/
        ├── local_isp.py    # Unchanged — LocalISP.pair() used by local-call
        └── twilio_client.py # Unchanged — make_outbound_call() used by call
```

### Pattern 1: Click Group with Shared Config Loading

**What:** A `@click.group()` defines the top-level `voice-agent` command with a `--config` flag. Each subcommand receives the loaded config via Click's context object.
**When to use:** Always — this is the correct Click pattern for multi-subcommand CLIs with shared state.

```python
# shuo/cli.py
import os
import click
import yaml
from dotenv import load_dotenv
from shuo.log import setup_logging

def _load_config(config_path: str | None) -> dict:
    """Load YAML config; auto-detect voice-agent.yaml in cwd if no path given."""
    path = config_path or "voice-agent.yaml"
    if os.path.exists(path):
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}

@click.group()
@click.option("--config", "-c", default=None, help="Path to YAML config file")
@click.pass_context
def cli(ctx, config):
    load_dotenv()
    setup_logging()
    ctx.ensure_object(dict)
    ctx.obj["config"] = _load_config(config)

def main():
    cli()
```

### Pattern 2: Flag-Wins Merge Per Subcommand

**What:** Load the per-command config section, then overwrite keys where CLI flags were explicitly provided (non-None).
**When to use:** Every subcommand — flags always win over config file values, which always win over hardcoded defaults.

```python
@cli.command()
@click.option("--port", default=None, type=int, help="Port to listen on")
@click.option("--drain-timeout", default=None, type=int, help="Seconds to wait for call drain on SIGTERM")
@click.pass_context
def serve(ctx, port, drain_timeout):
    cfg = ctx.obj["config"].get("serve", {})
    # Flag present (not None) always wins
    effective_port = port if port is not None else cfg.get("port", int(os.getenv("PORT", "3040")))
    effective_drain = drain_timeout if drain_timeout is not None else cfg.get("drain_timeout", 300)
    # ... reuse start_server() and SIGTERM handler from main.py
```

### Pattern 3: local-call with Concurrent run_conversation

**What:** Pair two `LocalISP` instances and run both `run_conversation()` coroutines concurrently with `asyncio.wait()`. When the first side completes (hangup), cancel the other.
**When to use:** `local-call` subcommand exclusively.

```python
import asyncio
from shuo.services.local_isp import LocalISP
from shuo.conversation import run_conversation

@cli.command("local-call")
@click.option("--caller-goal", default=None)
@click.option("--caller-identity", default=None)
@click.option("--callee-goal", default=None)
@click.option("--callee-identity", default=None)
@click.pass_context
def local_call(ctx, caller_goal, caller_identity, callee_goal, callee_identity):
    cfg = ctx.obj["config"].get("local_call", {})
    caller_cfg = {**cfg.get("caller", {})}
    callee_cfg = {**cfg.get("callee", {})}
    if caller_goal is not None:
        caller_cfg["goal"] = caller_goal
    if callee_goal is not None:
        callee_cfg["goal"] = callee_goal
    asyncio.run(_run_local_call(caller_cfg, callee_cfg))

def make_observer(label: str):
    def observer(event: dict):
        if event.get("type") == "transcript":
            print(f"[{label}]: {event['text']}", flush=True)
    return observer

async def _run_local_call(caller_cfg: dict, callee_cfg: dict):
    isp_caller = LocalISP()
    isp_callee = LocalISP()
    LocalISP.pair(isp_caller, isp_callee)  # MUST happen before start()

    task_caller = asyncio.create_task(
        run_conversation(
            isp_caller,
            observer=make_observer("CALLER"),
            get_goal=lambda _: caller_cfg.get("goal", ""),
        )
    )
    task_callee = asyncio.create_task(
        run_conversation(
            isp_callee,
            observer=make_observer("CALLEE"),
            get_goal=lambda _: callee_cfg.get("goal", ""),
        )
    )

    done, pending = await asyncio.wait(
        [task_caller, task_callee],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    print("\n[CALL ENDED]", flush=True)
```

### Pattern 4: pyproject.toml Entry Point

**What:** `[project.scripts]` in `pyproject.toml` registers the `voice-agent` command when installed.
**When to use:** Required — this is the delivery mechanism for all five CLI requirements.

```toml
# shuo/pyproject.toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "shuo"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = [
    "fastapi>=0.109.0",
    "uvicorn>=0.27.0",
    "websockets>=12.0",
    "twilio>=9.0.0",
    "numpy>=1.24.0",
    "python-dotenv>=1.0.0",
    "openai>=1.0.0",
    "deepgram-sdk>=3.0.0",
    "httpx>=0.27.0",
    "click>=8.0.0",
    "pyyaml>=6.0.0",
]

[project.scripts]
voice-agent = "shuo.cli:main"
```

After `pip install -e .` from `shuo/`, `voice-agent` is on `$PATH`.

### Pattern 5: bench Stub

**What:** Thin entry point that accepts the YAML dataset flag and prints a clear "not yet implemented" message. Phase 4 replaces the body with real benchmark runner logic.
**When to use:** `bench` subcommand in Phase 3.

```python
@cli.command()
@click.option("--dataset", default=None, help="YAML scenario file")
@click.pass_context
def bench(ctx, dataset):
    cfg = ctx.obj["config"].get("bench", {})
    effective_dataset = dataset or cfg.get("dataset")
    click.echo(f"bench: dataset={effective_dataset!r}")
    click.echo("Benchmark runner not yet implemented (Phase 4).")
```

### Anti-Patterns to Avoid

- **Passing the raw config dict through global state or env vars:** Use Click's `ctx.obj` to pass config cleanly to subcommands.
- **Calling `load_dotenv()` inside each subcommand:** Call it once in the `cli` group callback before any subcommand runs.
- **Setting `required=True` on Click options that have config file fallbacks:** All options should use `default=None` so the code can detect "was this flag explicitly provided?"
- **Calling `LocalISP.pair()` after `start()`:** `pair()` sets `_peer`; if `start()` runs first, `_peer` is None and audio is silently dropped.
- **Placing `pyproject.toml` in the repo root instead of `shuo/`:** The Python package root is `shuo/`; `pyproject.toml` must be co-located with `main.py`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Subcommand dispatch and routing | Manual `sys.argv[1]` if/elif | `@click.group()` + `@cli.command()` | Click generates help text, error messages, type coercion, `--help` for free |
| Flag presence detection | Separate boolean sentinel flags | `default=None` on Click options | Click returns `None` for unset options; one-line `if flag is not None:` check is sufficient |
| YAML loading and error reporting | Custom file parser | `yaml.safe_load()` + `yaml.YAMLError` | safe_load prevents arbitrary object execution from malicious config files |
| Async-from-sync bridging | Thread pool + queue bridge | `asyncio.run()` | Direct event loop; no thread overhead for I/O-bound async work |
| Server threading for `serve` | Custom thread management | Reuse `start_server()` + daemon thread pattern from `main.py` | SIGTERM drain logic, uvicorn wiring already battle-tested in production |

**Key insight:** Click + PyYAML cover 100% of CLI/config needs. All platform logic (`start_server`, `make_outbound_call`, `LocalISP.pair`, `run_conversation`) is already implemented and tested. The CLI is a thin composition layer.

---

## Common Pitfalls

### Pitfall 1: LocalISP pair-before-start ordering

**What goes wrong:** `isp_a._peer` is `None` until `LocalISP.pair(a, b)` is called. If either `start()` is called first, audio sent to the unpaired instance is silently dropped.

**Why it happens:** `LocalISP.pair()` is a class method that sets `_peer` on both instances. It has no guard against being called after `start()`.

**How to avoid:** Always call `LocalISP.pair(isp_caller, isp_callee)` before creating either `run_conversation()` task. The `asyncio.create_task()` calls schedule the coroutines but don't await them immediately — pairing before `create_task` is sufficient.

**Warning signs:** `local-call` runs silently with no transcript; agents appear to connect but never exchange audio.

### Pitfall 2: local-call conversation never terminates

**What goes wrong:** Both `run_conversation()` tasks run until a `StreamStopEvent` or `HangupRequestEvent` arrives. If neither agent sends `[HANGUP]` and the inactivity watchdog doesn't fire, the call runs forever.

**Why it happens:** Each `run_conversation()` exits only on `StreamStopEvent` (fired by `isp.stop()`) or `HangupRequestEvent`. `LocalISP.hangup()` fires the peer's `on_stop`, which puts `StreamStopEvent` into the peer's queue. The inactivity watchdog (from `CALL_INACTIVITY_TIMEOUT`) should terminate both sides eventually.

**How to avoid:** Use `asyncio.wait(return_when=asyncio.FIRST_COMPLETED)` so the CLI exits as soon as one side completes. Cancel and await the other task. Do not use `asyncio.gather()` alone — it waits for both tasks.

**Warning signs:** `voice-agent local-call` never returns to the shell prompt.

### Pitfall 3: call subcommand needs server running first

**What goes wrong:** `make_outbound_call()` registers `TWILIO_PUBLIC_URL/twiml` with Twilio. Twilio immediately fetches that URL to get TwiML. If the FastAPI server is not running, the call fails at Twilio's end with a webhook fetch error.

**Why it happens:** This is how the current `main.py` works too — it starts the server in a background thread, waits 2 seconds, then makes the call. The `call` subcommand must replicate this.

**How to avoid:** `voice-agent call` should start the server in a background daemon thread (identical to `serve`) before calling `make_outbound_call()`. Mirror `main.py` lines 97–107 exactly.

**Warning signs:** Call SID is returned successfully but Twilio console shows "Failed to connect" or webhook fetch errors.

### Pitfall 4: goal/identity not reaching Agent in local-call

**What goes wrong:** `run_conversation()` reads goal via `get_goal(call_sid)` if provided, otherwise falls back to `os.getenv("CALL_GOAL", "")`. If neither is set, the agent starts with an empty goal.

**Why it happens:** The conversation loop was designed for server use where goal often comes from per-call state. The CLI must inject goal explicitly.

**How to avoid:** Always pass `get_goal=lambda _: goal_string` to `run_conversation()`. Investigate `shuo/shuo/agent.py` before implementing to confirm how `identity` is consumed — it may be a constructor param or env var.

**Warning signs:** Agent in `local-call` starts with default/empty behavior, no goal-directed action.

### Pitfall 5: pyproject.toml in wrong directory

**What goes wrong:** Running `pip install -e .` from the repo root instead of `shuo/` fails to find the `shuo` package at the expected import path, so `voice-agent: command not found` or `ModuleNotFoundError: No module named 'shuo.cli'`.

**Why it happens:** The project layout has `voice-agent/shuo/` as the Python package root. `pyproject.toml` must live in `shuo/` alongside `main.py`.

**How to avoid:** Create `pyproject.toml` at `/path/to/voice-agent/shuo/pyproject.toml`. Install with `pip install -e .` from inside `shuo/`, or `pip install -e ./shuo` from the repo root. Verify: `pip show shuo` should show `Location: .../voice-agent/shuo`.

### Pitfall 6: Twilio env var check for non-Twilio subcommands

**What goes wrong:** `check_environment()` from `main.py` validates all seven env vars including Twilio credentials. Calling it in `local-call` fails in any dev environment where Twilio isn't configured.

**Why it happens:** `local-call` uses `LocalISP` — no Twilio vars are needed.

**How to avoid:** Create per-subcommand env checks. `serve` and `call` need Twilio vars. `local-call` needs only `DEEPGRAM_API_KEY`, `GROQ_API_KEY`, `ELEVENLABS_API_KEY`. `bench` (Phase 3 stub) needs none.

---

## Code Examples

Verified patterns from existing source:

### Reusable: start_server from main.py
```python
# Source: shuo/main.py lines 65–75
def start_server(port: int) -> None:
    global _uvicorn_server
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    _uvicorn_server = uvicorn.Server(config)
    _uvicorn_server.run()
```
Import and call directly from `cli.py`; no need to duplicate.

### Reusable: SIGTERM drain handler from main.py
```python
# Source: shuo/main.py lines 110–144
# signal.signal(signal.SIGTERM, _handle_sigterm) — must be registered on main thread
# Polls server_module._active_calls until drain or DRAIN_TIMEOUT elapses
# Then sets _uvicorn_server.should_exit = True
```
This is reusable as-is. Move the handler function into `cli.py`'s `serve` command body.

### Reusable: make_outbound_call signature
```python
# Source: shuo/shuo/services/twilio_client.py lines 18–47
def make_outbound_call(to_number: str) -> str:
    """Returns call SID. Reads all Twilio config from env vars."""
```

### Key run_conversation parameters for local-call
```python
# Source: shuo/shuo/conversation.py lines 69–81
async def run_conversation(
    isp,
    observer: Optional[Callable[[dict], None]] = None,   # transcript events → stdout
    get_goal: Optional[Callable[[str], str]] = None,      # goal per call_sid
    on_hangup: Optional[Callable[[], None]] = None,
    # ... other optional params
) -> None:
```

### Observer event types emitted by run_conversation
```python
# "transcript"   → {"type": "transcript", "speaker": "callee", "text": "..."}
# "stream_start" → {"type": "stream_start", "call_sid": ..., "stream_sid": ..., "phone": ...}
# "stream_stop"  → {"type": "stream_stop"}
# "agent_token"  → {"type": "agent_token", "token": "..."}   (streaming tokens)
# "agent_done"   → {"type": "agent_done"}
# "phase_change" → {"type": "phase_change", "from": "...", "to": "..."}
#
# NOTE: "speaker": "callee" is hardcoded in conversation.py for all transcript events.
# The local-call observer must use the label derived from WHICH isp instance the
# observer was registered for (caller side vs callee side), not the speaker field.
```

### YAML config schema (from CONTEXT.md decisions)
```yaml
serve:
  port: 3040
  drain_timeout: 300
call:
  goal: "Check account balance"
  identity: "Account holder"
  phone: "+1234567890"
local_call:
  caller:
    goal: "Ask about account balance"
    identity: "Customer"
  callee:
    goal: "Answer banking questions"
    identity: "Bank agent"
bench:
  dataset: scenarios.yaml
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `setup.py` with `entry_points` | `pyproject.toml` with `[project.scripts]` | PEP 517/518 (2017), PEP 621 (2021) | `pyproject.toml` is the standard; `setup.py` is deprecated |
| `asyncio.get_event_loop().run_until_complete()` | `asyncio.run()` | Python 3.7 (2018) | `asyncio.run()` creates a fresh loop, handles cleanup; use it |
| `python main.py +1234567890` | `voice-agent call +1234567890 --goal "..."` | Phase 3 | Clean subcommand interface with named flags |

**Deprecated/outdated:**
- `setup.py` entry_points: Replaced by `[project.scripts]` in `pyproject.toml`. Do not create a `setup.py`.
- `asyncio.get_event_loop()` as main entry: Deprecated in Python 3.10. Use `asyncio.run()`.

---

## Open Questions

1. **How `identity` flows into Agent**
   - What we know: `run_conversation()` has no `get_identity` parameter. `Agent()` is constructed inside `run_conversation()` with `goal=goal` (line 175 of `conversation.py`). There is no `identity` param visible in the `run_conversation()` signature.
   - What's unclear: Whether `identity` is a separate `Agent` constructor arg, an env var (`CALL_IDENTITY`?), or embedded in the goal string at call time.
   - Recommendation: Read `shuo/shuo/agent.py` before implementing `call` and `local-call`. If identity is not a `run_conversation()` param, pass it via env var set before `asyncio.run()`, or extend `run_conversation()` with a `get_identity` callback (a small, backward-compatible change).

2. **`voice-agent call` — whether to block until call ends**
   - What we know: `main.py` makes the outbound call then loops `while True: time.sleep(1)` until `KeyboardInterrupt`. The call itself is handled by inbound WebSocket from Twilio.
   - What's unclear: Should `voice-agent call` return immediately after placing the call, or block until the call ends?
   - Recommendation: Mirror current `main.py` behavior — block until `KeyboardInterrupt` so the server stays alive for the duration of the call.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| Config file | None detected (no pytest.ini; asyncio mode set via `@pytest.mark.asyncio`) |
| Quick run command | `cd /path/to/voice-agent/shuo && python -m pytest tests/test_cli.py -q` |
| Full suite command | `cd /path/to/voice-agent/shuo && python -m pytest tests/ --ignore=tests/test_bug_fixes.py -q` |

**Current test status (2026-03-21):** 34 tests pass. One pre-existing failure in `test_bug_fixes.py::test_dtmf_pending_sequential` due to missing `dashboard` module (`from dashboard.server import router` in `server.py`) — unrelated to Phase 3.

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CLI-01 | `serve` invokes uvicorn on configured port | unit (mock uvicorn) | `pytest tests/test_cli.py::test_serve_starts_server -x` | ❌ Wave 0 |
| CLI-02 | `call <phone>` invokes `make_outbound_call` with correct phone | unit (mock twilio_client) | `pytest tests/test_cli.py::test_call_invokes_outbound -x` | ❌ Wave 0 |
| CLI-03 | `local-call` runs two paired conversations, prints transcript | integration (no network) | `pytest tests/test_cli.py::test_local_call_runs -x` | ❌ Wave 0 |
| CLI-04 | `bench` accepts `--dataset` flag, prints stub message | unit | `pytest tests/test_cli.py::test_bench_stub -x` | ❌ Wave 0 |
| CLI-05 | YAML config values used when flags absent; flags override | unit (parametrized) | `pytest tests/test_cli.py::test_config_flag_override -x` | ❌ Wave 0 |

**Test tooling note:** Use `click.testing.CliRunner` for all CLI tests — it captures stdout and isolates filesystem. Example:
```python
from click.testing import CliRunner
from shuo.cli import cli

def test_bench_stub():
    runner = CliRunner()
    result = runner.invoke(cli, ["bench", "--dataset", "test.yaml"])
    assert result.exit_code == 0
    assert "not yet implemented" in result.output
```

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_cli.py -q`
- **Per wave merge:** `python -m pytest tests/ --ignore=tests/test_bug_fixes.py -q`
- **Phase gate:** Full suite (34 existing + new CLI tests) green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `shuo/tests/test_cli.py` — covers CLI-01 through CLI-05 using `CliRunner`
- [ ] `shuo/pyproject.toml` — required for entry point registration (not a test file, but needed before any CLI test can import `shuo.cli`)

---

## Sources

### Primary (HIGH confidence)
- Direct code inspection: `shuo/main.py`, `shuo/shuo/conversation.py`, `shuo/shuo/services/local_isp.py`, `shuo/shuo/services/twilio_client.py` — all reusable assets confirmed by reading source
- `importlib.metadata` — click 8.3.1, pyyaml 6.0.3, uvicorn 0.41.0 confirmed installed 2026-03-21
- `python3 --version` — 3.14.2 confirmed
- `python -m pytest` — 34 tests pass (excluding pre-existing dashboard env issue), pytest 9.0.2

### Secondary (MEDIUM confidence)
- Click 8.x group/context pattern — standard documented API, confirmed present in installed version
- PEP 621 `[project.scripts]` — canonical Python packaging standard, widely adopted

### Tertiary (LOW confidence)
- None

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all packages confirmed installed with exact versions
- Architecture: HIGH — patterns derived directly from reading all referenced source files; no guesswork
- Pitfalls: HIGH — derived from actual code behavior (pair-before-start ordering in LocalISP, SIGTERM threading in main.py, goal-via-env pattern in conversation.py)

**Research date:** 2026-03-21
**Valid until:** 2026-06-21 (stable domain — Click 8.x, PyYAML 6.x, uvicorn are not fast-moving)
