# Phase 3: CLI - Research

**Researched:** 2026-03-21
**Domain:** Python CLI frameworks, pyproject.toml packaging, YAML config loading, asyncio in CLI context
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Config file holds **behavioral settings only** — credentials stay in env vars
- Structure uses **per-command sections** matching subcommand names (`serve`, `call`, `local_call`, `bench`)
- Default config location: `voice-agent.yaml` in the **current working directory** (auto-loaded if present; `--config` flag overrides)
- Flag values override config file values (flags win)
- Both `local-call` sides are **LLM agents** — caller agent vs callee agent (no IVR mock)
- Each agent has its own `goal` and `identity` via `local_call.caller` / `local_call.callee` in YAML (or `--caller-*` / `--callee-*` flags)
- Terminal output: **live interleaved transcript** with speaker labels, printed in real-time
- Call ends when a hangup is detected; a summary is printed on completion
- **`pyproject.toml`** with `[project.scripts]` entry: `voice-agent = "shuo.cli:main"`
- Installed via `pip install -e .` — `voice-agent` command available globally after install
- CLI module lives at **`shuo/cli.py`** (sibling to `main.py` inside the `shuo/` directory)
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

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| CLI-01 | `voice-agent serve` starts the backend server (equivalent to current `main.py`) | `start_server()`, `_handle_sigterm`, `check_environment()` in `main.py` are directly reusable; `uvicorn.run(app)` pattern documented below |
| CLI-02 | `voice-agent call <phone>` places an outbound call with `--goal` and `--identity` flags | `make_outbound_call()` in `twilio_client.py` is directly callable; goal/identity currently set via env vars that the CLI must pass through |
| CLI-03 | `voice-agent local-call` runs a call between two agents using `LocalISP` (no Twilio) | `LocalISP.pair()` + two `run_conversation()` coroutines via `asyncio.run()`; observer callback pattern for transcript printing |
| CLI-04 | `voice-agent bench` runs IVR benchmark scenarios from a YAML file | Stub/delegator acceptable for Phase 3; Phase 4 owns BENCH-01–05 |
| CLI-05 | All CLI commands accept YAML config files; flags are overrides | PyYAML 6.0.3 already installed; merge-then-override pattern documented below |
</phase_requirements>

## Summary

Phase 3 adds a `voice-agent` CLI entry point that wraps all existing platform capabilities. The codebase already has working `start_server()`, `make_outbound_call()`, `LocalISP.pair()`, and `run_conversation()` — this phase is primarily wiring, not new logic. The main technical decisions are framework choice, config-merge strategy, and how to run async conversation code from a synchronous CLI entry point.

Click 8.3.1 is already installed as a transitive dependency of uvicorn, making it the natural choice — zero additional dependency, battle-tested, excellent subcommand support. PyYAML 6.0.3 is also already installed. The config-merge pattern (load YAML, deep-merge with explicit flag values) is straightforward and covered by a well-established pattern shown below.

The `local-call` subcommand is the most complex: it must run two `run_conversation()` coroutines concurrently within a single `asyncio.run()` call, wire observer callbacks for real-time transcript printing, and detect when either side hangs up to terminate the other cleanly.

**Primary recommendation:** Use Click 8.3.1 (already installed), PyYAML 6.0.3 (already installed), and `asyncio.run()` for async subcommands. No new dependencies required.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| click | 8.3.1 | CLI framework — subcommands, flags, help text | Already installed (uvicorn dep); mature, well-documented, no type annotations required |
| pyyaml | 6.0.3 | YAML config file loading | Already installed; `yaml.safe_load()` is the standard pattern |
| python-dotenv | >=1.0.0 | `.env` file loading (already used in `main.py`) | `load_dotenv()` call must be preserved in CLI entry point |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| asyncio (stdlib) | 3.14 | Run async subcommands from sync Click entry point | `asyncio.run()` wraps `local-call` async coroutines |
| uvicorn | >=0.27.0 | Serves FastAPI app (already used) | `serve` subcommand passes `app` to `uvicorn.Config` |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| click | typer | Typer adds type hints, but it is not currently installed — adds a dependency for no functional gain here |
| click | argparse | argparse is stdlib but has no native subcommand group concept as clean as `@cli.command()` |
| pyyaml | tomllib | TOML is stdlib in 3.11+ but YAML is what was decided for config format |

**Installation:** No new installs required. Click and PyYAML are already present.

**Version verification (confirmed 2026-03-21):**
- `click`: 8.3.1 (installed, `pip show click`)
- `pyyaml`: 6.0.3 (installed, `pip show pyyaml`)
- Python runtime: 3.14.2

## Architecture Patterns

### Recommended Project Structure
```
shuo/
├── cli.py              # NEW — Click CLI entry point (shuo.cli:main)
├── main.py             # KEEP for backwards compatibility
└── shuo/
    ├── server.py       # FastAPI app (unchanged)
    ├── conversation.py # run_conversation() (unchanged)
    └── services/
        ├── local_isp.py    # LocalISP.pair() (unchanged)
        └── twilio_client.py # make_outbound_call() (unchanged)
```

### Pattern 1: Click Subcommand Group with YAML Config

**What:** A top-level `cli` group with a `--config` option. Each subcommand reads config at invocation time via `click.pass_context`.

**When to use:** Always — every subcommand gets config-file values as defaults.

**Example:**
```python
# shuo/cli.py
import os
import click
import yaml
from dotenv import load_dotenv
from shuo.log import setup_logging

def load_config(config_path: str) -> dict:
    """Load YAML config file; return empty dict if not found."""
    if config_path and os.path.exists(config_path):
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    default = "voice-agent.yaml"
    if os.path.exists(default):
        with open(default) as f:
            return yaml.safe_load(f) or {}
    return {}

@click.group()
@click.option("--config", "-c", default=None, help="Path to YAML config file")
@click.pass_context
def cli(ctx, config):
    load_dotenv()
    setup_logging()
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config)

def main():
    cli()
```

### Pattern 2: Flag-wins Merge

**What:** Load config section for the subcommand, then overwrite keys where the CLI flag was explicitly provided.

**When to use:** Every subcommand that reads values from YAML.

**Example:**
```python
@cli.command()
@click.option("--port", default=None, type=int)
@click.option("--drain-timeout", default=None, type=int)
@click.pass_context
def serve(ctx, port, drain_timeout):
    cfg = ctx.obj["config"].get("serve", {})
    # Flags win — only override when explicitly given (not None)
    if port is not None:
        cfg["port"] = port
    if drain_timeout is not None:
        cfg["drain_timeout"] = drain_timeout
    # Resolved values
    resolved_port = cfg.get("port", int(os.getenv("PORT", "3040")))
    resolved_drain = cfg.get("drain_timeout", 300)
    ...
```

### Pattern 3: asyncio.run() for Async Subcommands

**What:** `local-call` must run two concurrent `run_conversation()` coroutines. Wrap the async work in a single `asyncio.run()` call.

**When to use:** `local-call` subcommand; potentially others that need async execution.

**Example:**
```python
import asyncio
from shuo.services.local_isp import LocalISP
from shuo.conversation import run_conversation

@cli.command("local-call")
@click.option("--caller-goal", default="", help="Goal for the caller agent")
@click.option("--caller-identity", default="", help="Identity for the caller agent")
@click.option("--callee-goal", default="", help="Goal for the callee agent")
@click.option("--callee-identity", default="", help="Identity for the callee agent")
@click.pass_context
def local_call(ctx, caller_goal, caller_identity, callee_goal, callee_identity):
    cfg = ctx.obj["config"].get("local_call", {})
    # Merge: flag wins over config
    caller_cfg = cfg.get("caller", {})
    callee_cfg = cfg.get("callee", {})
    if caller_goal:
        caller_cfg["goal"] = caller_goal
    if caller_identity:
        caller_cfg["identity"] = caller_identity
    if callee_goal:
        callee_cfg["goal"] = callee_goal
    if callee_identity:
        callee_cfg["identity"] = callee_identity

    asyncio.run(_run_local_call(caller_cfg, callee_cfg))
```

### Pattern 4: Real-time Transcript Observer

**What:** Pass an `observer` callback to `run_conversation()` that prints transcript events as they arrive. Speaker label is derived from which conversation is printing.

**When to use:** `local-call` subcommand.

**Example:**
```python
def make_observer(label: str):
    def observer(event: dict):
        if event["type"] == "transcript":
            print(f"[{label}]: {event['text']}", flush=True)
    return observer

async def _run_local_call(caller_cfg: dict, callee_cfg: dict):
    isp_a = LocalISP()
    isp_b = LocalISP()
    LocalISP.pair(isp_a, isp_b)

    caller_obs = make_observer("CALLER")
    callee_obs = make_observer("CALLEE")

    # Inject goal/identity via env vars or get_goal callback
    os.environ["CALL_GOAL"] = caller_cfg.get("goal", "")

    task_a = asyncio.create_task(
        run_conversation(isp_a, observer=caller_obs)
    )
    task_b = asyncio.create_task(
        run_conversation(isp_b, observer=callee_obs)
    )

    done, pending = await asyncio.wait(
        [task_a, task_b],
        return_when=asyncio.FIRST_COMPLETED,
    )
    # Cancel the remaining task when the first side hangs up
    for t in pending:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    print("\n[CALL ENDED]", flush=True)
```

### Pattern 5: pyproject.toml Entry Point

**What:** Declare `[project.scripts]` so `pip install -e .` registers `voice-agent` on `$PATH`.

**When to use:** Required — this is the delivery mechanism for CLI-01 through CLI-05.

**Example:**
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

**Note:** `pyproject.toml` goes in `shuo/` (the directory containing `main.py` and the `shuo/` package), not in the repo root. This is confirmed by the CONTEXT.md canonical reference.

### Anti-Patterns to Avoid

- **Nesting asyncio event loops:** Never call `asyncio.run()` inside a function that is already inside a running event loop. For `local-call`, the entire async work goes in one top-level `asyncio.run()`.
- **Passing goal/identity only through env vars:** The `call` and `local-call` subcommands accept these as explicit flags. The env var `CALL_GOAL` fallback exists, but explicit flags should take precedence.
- **Reading config per-flag with `required=True`:** Config file provides defaults; flags should use `default=None` so the code can detect "was this flag explicitly set?"
- **Putting pyproject.toml in the repo root:** The package root is `shuo/`; `pyproject.toml` belongs there alongside `requirements.txt`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| YAML loading with error messages | Custom parser | `yaml.safe_load()` + try/except `yaml.YAMLError` | safe_load prevents arbitrary object execution; error type is specific |
| Subcommand dispatch | Manual `sys.argv` parsing + `if/elif` | `@cli.command()` + Click | Click handles help text, error messages, type coercion, `--help` for free |
| Flag presence detection | Boolean sentinels | `default=None` on Click options | Click distinguishes "not provided" (None) from "provided as empty string" |
| Running async from sync | Thread pool + queue | `asyncio.run()` | Direct event loop creation; no thread overhead for I/O-bound work |
| Config file search path | Custom discovery logic | Check `--config` flag → CWD `voice-agent.yaml` → empty dict | Two-step logic; no need for XDG or platform-specific paths |

**Key insight:** Click plus PyYAML already cover 100% of CLI + config needs. The real work is wiring existing `run_conversation()`, `start_server()`, and `make_outbound_call()` into subcommand bodies.

## Common Pitfalls

### Pitfall 1: goal/identity Not Reaching Agent in local-call

**What goes wrong:** `run_conversation()` reads `CALL_GOAL` from env on `StreamStartEvent`. If the CLI sets the env var after `asyncio.run()` begins, the race may be benign — but the clean approach is passing a `get_goal` callback.

**Why it happens:** `run_conversation()` was designed for server use where goal comes from env or `get_goal` callback at call time.

**How to avoid:** Pass `get_goal=lambda call_sid: caller_cfg.get("goal", "")` to `run_conversation()` rather than setting env vars. Same for identity — currently the Agent reads `CALL_IDENTITY` env var; the CLI should set it before `asyncio.run()` or use the callback.

**Warning signs:** `local-call` starts but agent greets with wrong identity or no goal.

### Pitfall 2: Both LocalISP Tasks Starting Before Pair

**What goes wrong:** If `isp_a.start()` is called before `LocalISP.pair(isp_a, isp_b)`, `isp_a._peer` is None and audio is silently dropped.

**Why it happens:** `pair()` must happen before `start()` on either instance.

**How to avoid:** Always call `LocalISP.pair(a, b)` before creating either `run_conversation()` task.

### Pitfall 3: asyncio.run() Called Inside Click's Context (Click 8 and async)

**What goes wrong:** If something higher up already has a running event loop (e.g., pytest-asyncio, Jupyter), calling `asyncio.run()` raises `RuntimeError: This event loop is already running`.

**Why it happens:** `asyncio.run()` creates and closes its own event loop; it cannot be called from within an already-running loop.

**How to avoid:** In the CLI entry point, `asyncio.run()` is called at the very top of the sync Click handler — no issue in production. In tests, use `pytest.mark.asyncio` and call the async function directly rather than going through Click's test runner.

### Pitfall 4: uvicorn Blocks the Main Thread in serve

**What goes wrong:** `uvicorn.Server.run()` is blocking. The existing `main.py` runs it in a daemon thread with `threading.Thread`. If the CLI tries to run it synchronously on the main thread, `SIGTERM` handling (signal.signal) still works, but `KeyboardInterrupt` handling may differ.

**Why it happens:** `uvicorn.run()` blocks until server exits.

**How to avoid:** Mirror the `main.py` pattern exactly: run uvicorn in a daemon thread, keep the main thread alive with a `while True: time.sleep(1)` loop and `KeyboardInterrupt` handler.

### Pitfall 5: pyproject.toml Missing from shuo/ Directory

**What goes wrong:** `pip install -e .` from the wrong directory installs nothing; `voice-agent` command not found.

**Why it happens:** `pyproject.toml` must be in the same directory as the top-level package.

**How to avoid:** Create `shuo/pyproject.toml`. The shuo package is at `shuo/shuo/`, so `pyproject.toml` at `shuo/` is the correct package root. Verify with `pip show shuo` after install.

### Pitfall 6: call Subcommand Needs Server Running

**What goes wrong:** `voice-agent call <phone>` calls `make_outbound_call()`, which registers a TwiML URL with Twilio. If no server is listening at that URL, the call fails at Twilio's end.

**Why it happens:** `make_outbound_call()` passes `TWILIO_PUBLIC_URL/twiml` — Twilio will fetch that URL immediately.

**How to avoid:** The `call` subcommand should start the server (same as `serve`) in a background thread before initiating the call. This matches the existing `main.py` behavior exactly.

## Code Examples

Verified patterns from existing source:

### Existing start_server (reuse as-is)
```python
# Source: shuo/main.py lines 65-75
def start_server(port: int) -> None:
    global _uvicorn_server
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    _uvicorn_server = uvicorn.Server(config)
    _uvicorn_server.run()
```

### Existing SIGTERM drain (reuse as-is)
```python
# Source: shuo/main.py lines 110-144
# _handle_sigterm polls server_module._active_calls until drain or timeout
# signal.signal(signal.SIGTERM, _handle_sigterm) must be called in main thread
```

### Existing make_outbound_call (reuse as-is)
```python
# Source: shuo/shuo/services/twilio_client.py lines 18-47
# make_outbound_call(to_number: str) -> str  (returns call SID)
# Reads TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, TWILIO_PUBLIC_URL from env
```

### Existing run_conversation signature (key callbacks)
```python
# Source: shuo/shuo/conversation.py lines 69-81
async def run_conversation(
    isp,
    observer: Optional[Callable[[dict], None]] = None,   # for transcript printing
    get_goal: Optional[Callable[[str], str]] = None,      # goal per call_sid
    on_hangup: Optional[Callable[[], None]] = None,
    ...
) -> None:
```

### Observer event types produced by run_conversation
```python
# "transcript"  → {"type": "transcript", "speaker": "callee", "text": "..."}
# "stream_start" → {"type": "stream_start", "call_sid": ..., "phone": ...}
# "stream_stop"  → {"type": "stream_stop"}
# "agent_token"  → {"type": "agent_token", "token": "..."}  (streaming)
# "agent_done"   → {"type": "agent_done"}
# Note: "speaker": "callee" is what conversation.py emits;
#       the local-call observer should override the label based on which
#       isp instance (caller vs callee) the observer was registered for.
```

### YAML config schema (as decided in CONTEXT.md)
```yaml
serve:
  port: 3040
  drain_timeout: 300
call:
  goal: "..."
  identity: "..."
  phone: "+1234567890"
local_call:
  caller:
    goal: "..."
    identity: "..."
  callee:
    goal: "..."
    identity: "..."
bench:
  dataset: scenarios.yaml
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `python main.py +1234567890` | `voice-agent call +1234567890 --goal "..."` | Phase 3 | Clean subcommand interface with named flags |
| Hard-coded goal in env var only | Config file + flag override | Phase 3 | Goal/identity configurable per-invocation |
| No `local-call` | `voice-agent local-call` via LocalISP | Phase 3 | No Twilio credentials needed for agent testing |

**Deprecated/outdated:**
- Direct `python main.py` invocation: remains for backwards compatibility but `voice-agent serve` is the documented entry point going forward.

## Open Questions

1. **Agent identity field in run_conversation**
   - What we know: `CALL_GOAL` env var is read in `conversation.py`; `goal` is passed to `Agent()` constructor at line 175
   - What's unclear: Where does `identity` get consumed — is it an `Agent` constructor param, an env var, or part of the goal string?
   - Recommendation: Read `shuo/shuo/agent.py` before implementing `call` and `local-call` to confirm how identity is injected; likely needs a `get_identity` callback or env var set before `asyncio.run()`.

2. **bench subcommand stub interface**
   - What we know: Phase 4 owns BENCH-01–05; Phase 3 just needs the CLI entry point
   - What's unclear: Should `bench` in Phase 3 print "not yet implemented" or silently succeed?
   - Recommendation: Implement as a thin stub that prints a clear "benchmark runner not yet available" message and exits 0; Phase 4 replaces the body.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 7.x + pytest-asyncio |
| Config file | none (no pytest.ini or setup.cfg detected) |
| Quick run command | `cd shuo && python3 -m pytest tests/ -q` |
| Full suite command | `cd shuo && python3 -m pytest tests/ -v` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CLI-01 | `voice-agent serve` invokes uvicorn | unit (Click test runner) | `pytest tests/test_cli.py::test_serve_starts_server -x` | ❌ Wave 0 |
| CLI-02 | `voice-agent call <phone>` calls `make_outbound_call` | unit (mock Twilio) | `pytest tests/test_cli.py::test_call_invokes_outbound -x` | ❌ Wave 0 |
| CLI-03 | `voice-agent local-call` runs two paired conversations | unit (async, no network) | `pytest tests/test_cli.py::test_local_call_runs -x` | ❌ Wave 0 |
| CLI-04 | `voice-agent bench` accepts YAML file arg | unit (stub verification) | `pytest tests/test_cli.py::test_bench_stub -x` | ❌ Wave 0 |
| CLI-05 | YAML config loaded; flags override config values | unit (parametrized) | `pytest tests/test_cli.py::test_config_flag_override -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `cd shuo && python3 -m pytest tests/ -q`
- **Per wave merge:** `cd shuo && python3 -m pytest tests/ -v`
- **Phase gate:** Full suite green (42 existing + new CLI tests) before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `shuo/tests/test_cli.py` — covers CLI-01 through CLI-05 (use `click.testing.CliRunner` for sync commands; `asyncio.run()` or pytest-asyncio for async internals)

## Sources

### Primary (HIGH confidence)
- Direct code inspection of `shuo/main.py`, `shuo/shuo/conversation.py`, `shuo/shuo/services/local_isp.py`, `shuo/shuo/services/twilio_client.py` — all reusable assets confirmed
- `pip show click` — version 8.3.1 confirmed installed 2026-03-21
- `pip show pyyaml` — version 6.0.3 confirmed installed 2026-03-21
- `python3 --version` — 3.14.2 confirmed
- `python3 -m pytest tests/ --collect-only` — 42 tests confirmed, no pytest.ini exists

### Secondary (MEDIUM confidence)
- Click official docs pattern for subcommand groups with `pass_context` — matches Click 8.x documented API
- `pyproject.toml` `[project.scripts]` pattern — PEP 517/518 standard, setuptools >=68 required

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — both libraries confirmed installed, versions verified
- Architecture: HIGH — based on direct reading of all referenced source files
- Pitfalls: HIGH — derived from actual code behavior (goal via env var, server threading model, pair-before-start ordering)

**Research date:** 2026-03-21
**Valid until:** 2026-06-21 (stable domain — Click 8.x, PyYAML 6.x are not fast-moving)
