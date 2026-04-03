## Context

The existing benchmark runs one agent against a static IVR YAML flow (DTMF menus). The `BenchISP` + `IVRDriver` pair drives that scenario. Real phone call capability — negotiation, identity verification, adaptive responses — can't be tested against a static menu.

The codebase already has `LocalISP.pair(a, b)` for in-process loopback and `run_conversation` as the single entry point for any agent instance. Both `_BenchFluxPool` and `_BenchTTSPool` bypass audio I/O, injecting text events directly. The challenge is routing text output from one agent as text input to the other.

## Goals / Non-Goals

**Goals:**
- Two full `run_conversation` instances talk to each other over loopback, with no real audio, TTS, or STT.
- Each agent's LLM-generated speech text is forwarded to the peer as a `FluxEndOfTurnEvent`.
- Scenarios are tiered by difficulty and defined in YAML.
- Each run produces a per-run report; a shared cumulative summary is appended.

**Non-Goals:**
- Real audio quality evaluation (latency, codec fidelity).
- More than two agents per scenario (future work).
- Changing how the IVR benchmark mode works.
- Grading semantic quality of responses (LLM-as-judge) — success is keyword/turn-count based, same as IVR mode.

## Decisions

### 1. Text bridge via observer callbacks

**Decision**: Capture each agent's finished-turn speech from its `observer` callback and inject it into the peer's `_inject` function as `FluxEndOfTurnEvent`.

The observer already receives `{"type": "transcript", "text": ...}` on every completed agent turn. This is the only place where the agent's finalized response text is available without modifying `agent.py`.

**Alternative**: Hook into `LLMService` or `AudioPlayer` directly for earlier access. Rejected — that would require modifying internal service code and would be fragile.

**Alternative**: Use real TTS audio through paired `LocalISP` queues and feed it to a no-op STT that echoes it back as text. Rejected — defeats the purpose of bypassing I/O; adds latency and API cost.

### 2. `_BenchFluxPool` extended to support bidirectional injection

**Decision**: The same `_BenchFluxPool` and `_BenchTTSPool` stubs are reused for both agents. Each agent gets its own instance so they don't share queue state.

The `_inject` callback set by `run_conversation` on each `BenchISP` is the injection point. The bridge reads `bench_isp.caller._inject` and `bench_isp.answerer._inject` after both tasks start.

### 3. Call initiation: synthetic "phone ringing" event

**Decision**: After both agents are ready (both `_inject` set), inject a synthetic `FluxEndOfTurnEvent(transcript="[call connected]")` into the caller's pipeline to trigger its opening line. The answerer waits for the caller to speak first.

**Alternative**: Start the answerer with a greeting (e.g., "Thank you for calling…"). Kept as configurable per-scenario via `answerer.opening_line` YAML field.

### 4. Difficulty encoded in YAML, not in code

**Decision**: `easy`, `medium`, `hard` are scenario-level metadata fields. The runner logs them in reports but applies no different logic. Difficulty manifests through scenario design — harder scenarios have more verification steps, partial/misleading caller info, stricter success criteria.

**Alternative**: Code-level difficulty modifiers (e.g., add noise to answerer LLM prompt). Rejected — premature complexity; difficulty should emerge from scenario craft.

### 5. Answerer goal string is the full configuration

**Decision**: The answerer agent is configured entirely by a `goal` string in the YAML, just like the caller. This avoids a new agent type. The goal encodes: service persona, allowed topics, required caller verification fields (account number, name, DOB), and refusal policy.

Example:
```
You are a bank customer service agent. You MUST verify the caller's account number and full name before discussing account details. If they cannot provide both, politely refuse and end the call.
```

### 6. Termination conditions

**Decision**: The conversation ends when:
1. Either agent calls `hangup()` (fires peer's `on_stop`), OR
2. `max_turns` is exceeded (orchestrator cancels both tasks), OR
3. Wall-clock timeout fires.

The bridge monitors total turn count. On termination, both tasks are cancelled and results evaluated.

### 7. Per-run report: JSON + Markdown sidecar files

**Decision**: Write `reports/<dataset_stem>_<timestamp>.json` and `.md` on every run. The `--summary` flag points to a shared Markdown file (default `reports/bench_summary.md`) that is opened in append mode after each run — no locking needed (single process).

**Alternative**: SQLite for cumulative data. Rejected — YAML/Markdown is consistent with the rest of the project; easy to read/diff in git.

## Risks / Trade-offs

- **Turn ordering race** → The bridge's observer callback injects peer events from inside an async task context. Both agents share the same event loop. If one agent's turn fires before the peer's `_inject` is set, the injection is silently dropped. Mitigation: poll until both `_inject` are set (same pattern as existing IVR scenario runner, up to 0.5s).

- **Infinite loop** → Two agents could keep talking indefinitely if neither hangs up. Mitigation: `max_turns` and wall-clock timeout enforced by the orchestrator; hard cap of 50 turns regardless.

- **Scenario flakiness from LLM non-determinism** → The same scenario may pass or fail across runs due to model stochasticity. Mitigation: document this in the shared summary (include pass/fail over N runs for trending); out of scope for v1 to add multiple-run averaging.

- **`_BenchTTSPool.flush()` timing** → The current no-op TTS pool calls `on_done()` in a background task to unblock the agent's RESPONDING state. With two agents, this must not race. Since each agent has its own pool instance, there is no shared state — risk is low.

## Migration Plan

1. Add `TwoAgentScenarioConfig` dataclass and `run_two_agent_scenario` / `run_two_agent_benchmark` functions to `bench.py` — no changes to existing IVR functions.
2. Add `--mode` and `--summary` flags to `voice-agent bench` CLI; default `--mode ivr` preserves existing behavior.
3. Add scenario YAML files in `scenarios/`.
4. Add `reports/` directory (`.gitkeep` + `.gitignore` for report files).
5. No migration needed — additive change, existing `bench` usage unchanged.

## Open Questions

- Should the answerer agent emit an opening greeting by default, or wait silently for the caller to speak first? (Current design: configurable via `answerer.opening_line`; empty = wait.)
- Should success criteria support regex patterns in `transcript_contains`? (Nice-to-have; not in v1.)
- Should the shared summary report also track model version / git SHA for reproducibility? (Recommended but not required for v1.)
