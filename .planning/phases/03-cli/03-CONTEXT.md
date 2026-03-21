# Phase 3: CLI - Context

**Gathered:** 2026-03-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Deliver a `voice-agent` CLI command covering `serve`, `call`, `local-call`, and `bench` subcommands. All commands accept a YAML config file; CLI flags are overrides. Depends on LocalISP (Phase 1). IVR benchmark runner logic (metrics, scenario YAML schema) is Phase 4 — `bench` in this phase is the CLI entry point only.

</domain>

<decisions>
## Implementation Decisions

### CLI framework
- Claude's discretion — user did not specify a framework preference

### Config file schema
- Config file holds **behavioral settings only** — credentials stay in env vars
- Structure uses **per-command sections** matching subcommand names:
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
- Default config location: `voice-agent.yaml` in the **current working directory** (auto-loaded if present; `--config` flag overrides)
- Flag values override config file values (flags win)

### local-call behavior
- Both sides are **LLM agents** — caller agent vs callee agent (no IVR mock involvement)
- Each agent has its own `goal` and `identity`, configured via the `local_call.caller` / `local_call.callee` sections in YAML (or `--caller-*` / `--callee-*` flags)
- Terminal output: **live interleaved transcript** with speaker labels, printed in real-time:
  ```
  [CALLER]: I need to check my account balance.
  [CALLEE]: Sure, please provide your account number.
  ```
- Call ends when a hangup is detected; a summary is printed on completion

### Package entry point
- **`pyproject.toml`** with `[project.scripts]` entry: `voice-agent = "shuo.cli:main"`
- Installed via `pip install -e .` — `voice-agent` command available globally after install
- CLI module lives at **`shuo/cli.py`** (top level of the shuo directory, sibling to `main.py`)
- `main.py` can remain for backwards compatibility or be deprecated

### Claude's Discretion
- CLI framework choice (Click, argparse, Typer — all are compatible with Python 3.9+)
- pyproject.toml package metadata (name, version, dependencies)
- Exact flag names and short flags for each subcommand
- How `voice-agent bench` wires into the Phase 4 benchmark runner (stub or thin delegator acceptable for this phase)
- How `local-call` transcript output is formatted beyond speaker labels (timestamps, colors, etc.)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### CLI requirements
- `.planning/REQUIREMENTS.md` §CLI — CLI-01 through CLI-05: formal requirements with acceptance criteria

### Existing entry point to replace/wrap
- `shuo/main.py` — current entry point; `serve` + outbound call logic to migrate into CLI subcommands
- `shuo/shuo/server.py` — FastAPI app; `serve` subcommand starts this via uvicorn
- `shuo/shuo/services/twilio_client.py` — `make_outbound_call()` used by `call` subcommand

### LocalISP (required for local-call)
- `shuo/shuo/services/local_isp.py` — `LocalISP.pair()` connects two instances; `local-call` pairs two VoiceSession instances

### Conversation entry point
- `shuo/shuo/conversation.py` — `run_conversation()` is what `local-call` needs to invoke for each agent side

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `shuo/main.py` — `start_server()`, `check_environment()`, graceful SIGTERM drain logic — all reusable in `serve` subcommand
- `shuo/shuo/services/twilio_client.py` — `make_outbound_call()` — directly usable in `call` subcommand
- `shuo/shuo/services/local_isp.py` — `LocalISP.pair()` — directly usable in `local-call` subcommand
- `shuo/shuo/conversation.py` — `run_conversation()` — what each agent side of `local-call` calls

### Established Patterns
- Env vars for credentials (TWILIO_ACCOUNT_SID, DEEPGRAM_API_KEY, etc.) — config file does NOT carry these
- `dotenv` (`load_dotenv()`) used in `main.py` — CLI should preserve this
- `PORT` env var for server port — config file `serve.port` overrides this default

### Integration Points
- `shuo/shuo/server.py` FastAPI `app` — `serve` subcommand passes it to `uvicorn.Config`
- `shuo/shuo/log.py` — `setup_logging()` and `Logger.*` — CLI should call `setup_logging()` before any subcommand runs
- pyproject.toml must be created in `shuo/` directory (where `main.py` and the `shuo/` package live)

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 03-cli*
*Context gathered: 2026-03-21*
