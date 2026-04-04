## Why

The current benchmark only tests agent-vs-IVR navigation (DTMF menus, static flows), which doesn't capture how the agent performs in real conversational phone calls where another intelligent agent is on the other end. We need a two-agent eval harness — caller and answerer — that exercises the full conversational loop, identity verification, and goal completion at a range of difficulty levels.

## What Changes

- **New**: Two-agent evaluation mode where one agent instance calls another over the local loopback ISP (`LocalISP`), replacing the IVR-driver with a live answerer agent.
- **New**: Answerer agent role — configurable service scope, knowledge base, and verification gates that gate responses on caller providing sufficient identification/context.
- **New**: Difficulty-tiered scenario suite (`scenarios/two_agent_*.yaml`) covering easy (direct Q&A), medium (requires verification), and hard (multi-step, partial info, misdirection).
- **New**: Per-run JSON + Markdown report saved alongside the dataset file; shared cumulative `bench_summary.md` that is appended after every run.
- **Modified**: `voice-agent bench` CLI accepts a `--mode` flag (`ivr` existing default, `two-agent` new) and `--summary` flag to specify where the shared summary file lives.

## Capabilities

### New Capabilities

- `two-agent-eval`: Orchestration harness that runs two simultaneous `run_conversation` instances connected via `LocalISP` loopback, driving caller goal → answerer response cycles and capturing full bilateral transcript.
- `agent-answerer`: Answerer agent role definition — YAML-configured service persona with knowledge scope, verification requirements, and topic gates; implemented as a goal string injected into the second agent instance.
- `bench-scenario-suite`: Tiered scenario YAML files (`easy`, `medium`, `hard`) for the two-agent eval, with richer success criteria: goal-completion keywords, max-turns, and verification-passed flags.
- `bench-reporting`: Per-run structured report (JSON + Markdown) and appendable shared `bench_summary.md` accumulating pass rates, difficulty breakdowns, and trend data across runs.

### Modified Capabilities

- `bench`: Existing `voice-agent bench` command gains `--mode` and `--summary` flags; `run_benchmark` is extended to dispatch to the new two-agent runner while keeping IVR mode unchanged. **BREAKING** (additive CLI flag, no breakage for existing usage).

## Impact

- `shuo/shuo/bench.py`: Extended with `TwoAgentScenarioConfig`, `run_two_agent_scenario`, `run_two_agent_benchmark` functions.
- `shuo/shuo/cli.py`: `bench` command gains `--mode` and `--summary` options.
- `shuo/shuo/services/local_isp.py`: Verify loopback supports bidirectional duplex needed for two talking agents (likely already works; confirm).
- `scenarios/`: New YAML files `two_agent_easy.yaml`, `two_agent_medium.yaml`, `two_agent_hard.yaml`.
- `reports/`: Runtime output directory for per-run reports (created on first run).
- No new dependencies required; uses existing `run_conversation`, `LocalISP`, `httpx`, `yaml`, `json`.
