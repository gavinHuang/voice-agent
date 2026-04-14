## Why

The existing IVR benchmark only tests happy-path navigation through static YAML flow files. Real-world IVR systems exhibit edge cases — out-of-hours messages, hold queues, unexpected human pickup — that the agent must handle gracefully, and these are never exercised today. A scenario synthesizer fills this gap by generating realistic edge-case flows on-demand, exposing agent robustness gaps before production.

## What Changes

- **New `ivr-scenario-synthesizer` module** (`simulator/synthesizer.py`): programmatically generates IVR flow YAML files covering a library of edge-case patterns (out-of-hours, hold queue, premature human pickup, DTMF timeout loop, menu repetition cap, etc.).
- **New `synthesize` CLI sub-command** (`voice-agent ivr-synthesize`): generates one or more synthetic scenario + flow YAML pairs and writes them to disk for use with `voice-agent bench`.
- **New benchmark dataset** (`eval/scenarios/synthesized_edge_cases.yaml`): pre-generated set of edge-case scenarios produced by the synthesizer, usable out-of-box without running synthesis at bench time.
- **Extended simulator flow node types**: add `hold` node type (plays hold music / repeating message for configurable duration) and `out-of-hours` node type (plays closed message then hangs up) to the simulator engine to support synthesized flows.
- **Extended bench runner** (`shuo/bench.py`): accept a `--synthesize` flag that auto-generates fresh edge-case flows before running, so every bench run exercises a different random variant.

## Capabilities

### New Capabilities
- `ivr-scenario-synthesizer`: Generates IVR flow YAML + matching benchmark scenario YAML covering edge-case patterns. Supports parameterized templates (hold duration, queue depth, out-of-hours message, human-pickup timing).

### Modified Capabilities
- `cli-call`: Extended with `ivr-synthesize` sub-command entry point (new CLI surface, not a requirement change to existing call behavior).

## Impact

- **`simulator/`**: new `synthesizer.py` module; `engine.py` + `config.py` gain `hold` and `out-of-hours` node types.
- **`shuo/bench.py`** / **`shuo/cli.py`**: `--synthesize` flag and `ivr-synthesize` sub-command wired in.
- **`eval/scenarios/`**: new pre-built edge-case scenario dataset.
- **No breaking changes** to existing flow YAML format or benchmark datasets.
- **No new external dependencies** — synthesis is pure Python using stdlib `random` + template strings.
