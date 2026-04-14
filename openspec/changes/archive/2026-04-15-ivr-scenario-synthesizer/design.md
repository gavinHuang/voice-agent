## Context

The IVR simulator (`simulator/`) serves as a Twilio-compatible call flow server driven by YAML node graphs. Benchmarking (`shuo/bench.py`, `voice-agent bench`) runs the voice agent against these flows and checks transcripts/DTMF sequences against scenario success criteria defined in `eval/scenarios/*.yaml`.

Currently, all flows are hand-authored and only cover happy-path navigation. Edge cases (out-of-hours, hold queues, human pickup interrupting IVR, DTMF loops with no exit) are never tested. Synthesizing these flows programmatically eliminates the manual authoring burden and can randomize parameters on each run to prevent overfitting.

## Goals / Non-Goals

**Goals:**
- New `simulator/synthesizer.py` module producing valid YAML for both flow graphs and matching benchmark scenario files covering a fixed library of edge-case patterns.
- New `hold` and `out-of-hours` simulator node types, extending `simulator/config.py` + `simulator/engine.py`.
- New `voice-agent ivr-synthesize` CLI command that writes generated files to disk.
- Optional `--synthesize` flag on `voice-agent bench` that auto-generates and loads fresh flows before running.
- Pre-built `eval/scenarios/synthesized_edge_cases.yaml` committed to the repo so CI can run edge-case benchmarks without invoking synthesis.

**Non-Goals:**
- LLM-based or ML-based scenario generation — templates + parameterization only.
- Synthesis of non-IVR (two-agent) flows.
- Modifying success-criteria evaluation logic in the bench runner.

## Decisions

### 1. Template-based synthesis over LLM generation
**Decision:** Use pure Python template functions, each producing a named edge-case flow + scenario, parameterized with random values (durations, messages, queue depths).

**Rationale:** Deterministic, zero latency, no API cost. Scenarios can still be randomized by seeding `random`. LLM generation would add non-determinism and require a round-trip that slows the bench setup path.

**Alternative considered:** YAML Jinja2 templates — adds a dependency and the templates become harder to unit-test than plain Python functions.

### 2. New node types rather than composite existing nodes
**Decision:** Add `hold` and `out-of-hours` as first-class node types in `simulator/config.py` and `simulator/engine.py`.

**Rationale:** A `hold` loop could theoretically be expressed as a `pause → redirect` chain, but that produces dozens of nodes for realistic queue depths, bloating generated flow files. A native `hold` node with `repeat` and `duration` fields keeps the YAML readable and the engine logic clean.

**Alternative considered:** Implement hold purely via looping `pause` + `say` nodes — rejected due to node explosion.

### 3. Synthesizer as standalone module, not a CLI-only script
**Decision:** `simulator/synthesizer.py` exposes a Python API (`synthesize(patterns, seed) → (flow_yaml, scenario_yaml)`) that the CLI wraps; it is also importable by tests.

**Rationale:** Enables unit-testing synthesis logic without subprocess overhead. The CLI command is a thin wrapper.

### 4. `--synthesize` flag generates to a temp dir, not overwriting committed files
**Decision:** When `voice-agent bench --synthesize` is run, flows and scenarios are written to a temp directory and passed to the bench runner directly; committed files in `eval/scenarios/` are not touched.

**Rationale:** Prevents CI state pollution. The committed `synthesized_edge_cases.yaml` is a snapshot, not regenerated on every run.

## Risks / Trade-offs

- **`hold` node adds simulator complexity** → Mitigation: keep the TwiML rendering simple — a `hold` node renders as `<Play loop="N">` of silence + `<Say>` repeat, or just a long `<Pause>` chain. Review after first implementation.
- **Randomized parameters make benchmark results non-reproducible across runs** → Mitigation: `--seed` flag on `ivr-synthesize` and `bench --synthesize`; seed logged with bench output.
- **New node types not recognized by existing YAML validators / tests** → Mitigation: update `simulator/tests/` to cover new types; `config.py` parse path remains additive.
- **Agent not trained to handle hold audio as distinct from silence** → Not a synthesizer risk — this surfaces agent gaps, which is the point.

## Migration Plan

1. Extend `simulator/config.py` + `simulator/engine.py` with `hold` / `out-of-hours` types (backward-compatible, existing flows unchanged).
2. Add `simulator/synthesizer.py`.
3. Wire `voice-agent ivr-synthesize` into `shuo/cli.py`.
4. Add `--synthesize` flag to `bench` command.
5. Commit pre-built `eval/scenarios/synthesized_edge_cases.yaml` by running `voice-agent ivr-synthesize --output eval/`.
6. Update `simulator/tests/` for new node types.

Rollback: revert steps 1–4; committed scenario file is inert if `--synthesize` flag is absent.

## Open Questions

- Should `hold` play actual audio (silence MP3 / hold music URL) or just `<Pause>`? A `<Pause>` is simplest and sufficient for latency testing.
- Should the synthesizer also generate negative scenarios (agent *fails* correctly, e.g. hangs up when IVR is out-of-hours)? Could be a v2 addition.
