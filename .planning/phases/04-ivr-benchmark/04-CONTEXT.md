# Phase 4: IVR Benchmark - Context

**Gathered:** 2026-03-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Build a repeatable benchmark suite that evaluates how reliably the LLM agent navigates IVR systems. Deliverables: YAML scenario schema, a benchmark runner wired into `voice-agent bench --dataset`, structured metrics output, and 3+ sample scenarios covering the example IVR flow. Uses LocalISP (Phase 1) and the CLI entry point (Phase 3). Twilio credentials are NOT required to run benchmarks.

</domain>

<decisions>
## Implementation Decisions

### Scenario YAML schema

- **Agent config is inline per-scenario** — each scenario has its own `agent: {goal, identity}` block; scenarios are self-contained
- **ALL success criteria must pass** for a scenario to be marked passing — no `pass_when: any` mode
- **Optional `ivr_flow` field** — if absent, defaults to `ivr/flows/example.yaml`; allows future flows without schema changes
- Schema shape:
  ```yaml
  scenarios:
    - id: "navigate-to-sales"
      description: "Agent navigates to sales department via DTMF"
      agent:
        goal: "Navigate the IVR to reach the sales department"
        identity: "Customer"
      ivr_flow: ivr/flows/example.yaml   # optional; defaults to example.yaml
      timeout: 30
      success_criteria:
        transcript_contains:
          - "sales"
        dtmf_sequence: "1"
        max_turns: 5
  ```

### IVR coupling mode

- **HTTP loopback** — the IVR FastAPI app (`ivr/server.py`) starts on a local port for each benchmark run; the IVR driver makes HTTP requests to it (`/twiml`, `/ivr/step`, `/ivr/gather`)
- **Text injection — bypass TTS** — the IVR driver extracts `<Say>` text from TwiML responses and injects it directly into the agent's transcript via the conversation observer/event system; no TTS API calls needed
- **DTMF routing via LocalISP** — the IVR driver monitors `LocalISP._inject` callback to receive DTMF events from the agent, then makes the corresponding HTTP POST to `/ivr/gather?node=ID`; LocalISP remains the communication bus

### Claude's Discretion

- Where the benchmark runner module lives (`shuo/shuo/bench.py` or `shuo/shuo/services/bench.py` — Claude decides)
- Metrics report format (terminal table per BENCH-04; optional JSON file output if `--output` flag provided)
- Which 3 sample scenarios to include (should cover: happy-path DTMF navigation, multi-step menu traversal, and a timeout/failure case)
- Port selection for the IVR loopback server (ephemeral/random to avoid conflicts)
- Whether scenarios run sequentially or in parallel within a single `bench` invocation
- Exact IVR driver implementation (how TwiML XML is parsed, how state machine tracks current node)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Benchmark requirements
- `.planning/REQUIREMENTS.md` §IVR Benchmark — BENCH-01 through BENCH-05: formal requirements with acceptance criteria

### IVR mock server (the IVR side of the pair)
- `ivr/server.py` — FastAPI IVR mock; endpoints `/twiml`, `/ivr/step?node=ID`, `/ivr/gather?node=ID`
- `ivr/engine.py` — `TwiMLEngine` that drives node traversal and generates TwiML XML
- `ivr/config.py` — `load_config()` and `IVRConfig` — how flow YAML is loaded
- `ivr/flows/example.yaml` — the default IVR flow with menu nodes, DTMF routes, and `<Say>` text

### CLI entry point (Phase 3 stub to wire up)
- `shuo/shuo/cli.py` — `bench` subcommand stub; runner logic replaces the stub body
- `.planning/phases/03-cli/03-CONTEXT.md` — CLI config schema: `bench: {dataset: scenarios.yaml}`

### LocalISP (agent ↔ IVR audio bus)
- `shuo/shuo/services/local_isp.py` — `LocalISP.pair()`, `_inject` callback for DTMF routing
- `shuo/shuo/conversation.py` — `run_conversation()` — how the agent side is driven

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `ivr/server.py` — complete IVR mock; can be started as a real HTTP server on a loopback port via uvicorn for each benchmark run
- `ivr/flows/example.yaml` — ready-made IVR flow; 3 sample scenarios should exercise its nodes (`main_menu`, `sales_greeting`, `support_greeting`, etc.)
- `LocalISP.pair()` + `_inject` callback — already established pattern from `local-call`; IVR driver uses the same pairing mechanism
- `run_conversation()` with `observer` callback — used by `local-call`; same pattern for the agent side of each benchmark scenario
- `click.testing.CliRunner` + existing `test_cli.py` — test patterns already established; benchmark tests extend these

### Established Patterns
- HTTP loopback pattern exists in `shuo/shuo/server.py` (`start_server()` in daemon thread) — IVR server can start the same way
- Observer callback pattern for transcript events — `local-call` uses `_make_observer`; benchmark extends this to collect transcript for `transcript_contains` evaluation
- YAML loading via `yaml.safe_load()` — used in CLI config loading; same for scenario files
- `asyncio.wait(FIRST_COMPLETED)` — used by `local-call` for termination; benchmark runner uses the same for per-scenario timeout enforcement

### Integration Points
- `shuo/shuo/cli.py` `bench` command body — stub currently prints "not yet implemented"; Phase 4 replaces this with `asyncio.run(run_benchmark(dataset, ...))` call
- `shuo/shuo/conversation.py` `run_conversation()` — agent side of each scenario
- `LocalISP._inject` — IVR driver sets this on the agent-side ISP to receive DTMF events

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

*Phase: 04-ivr-benchmark*
*Context gathered: 2026-03-21*
