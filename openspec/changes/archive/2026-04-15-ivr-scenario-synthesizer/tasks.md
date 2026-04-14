## 1. Extend Simulator Node Types

- [x] 1.1 Add `hold` node dataclass fields (`repeat: int`, `interval: int`, `next: str`) to `simulator/config.py:Node` and update `parse_config()` to populate them
- [x] 1.2 Add `out-of-hours` node dataclass fields (`say: str`) to `simulator/config.py:Node` and update `parse_config()` to populate them
- [x] 1.3 Implement `_render_hold()` in `simulator/engine.py:TwiMLEngine` — renders `<Pause length="{interval}"/>` with a counter-based redirect loop terminating at `next`
- [x] 1.4 Implement `_render_out_of_hours()` in `simulator/engine.py:TwiMLEngine` — renders `<Say>{message}</Say><Hangup/>`
- [x] 1.5 Wire `hold` and `out-of-hours` into `TwiMLEngine.render_node()` dispatch

## 2. Simulator Tests for New Node Types

- [x] 2.1 Add unit tests in `simulator/tests/` for `parse_config()` parsing `hold` and `out-of-hours` nodes from YAML
- [x] 2.2 Add unit tests for `TwiMLEngine._render_hold()` verifying correct `<Pause>` length and redirect loop behaviour
- [x] 2.3 Add unit tests for `TwiMLEngine._render_out_of_hours()` verifying `<Say>` + `<Hangup/>` output

## 3. Scenario Synthesizer Module

- [x] 3.1 Create `simulator/synthesizer.py` with a `SynthesisResult` datatype holding `(flow_yaml: str, scenario_yaml: str, pattern: str)`
- [x] 3.2 Implement `synthesize(patterns: list[str] | None, seed: int | None) -> list[SynthesisResult]` as the public API entry point
- [x] 3.3 Implement `_synth_out_of_hours(rng)` — generates an out-of-hours flow (welcome → out-of-hours → hangup) with randomized closed message
- [x] 3.4 Implement `_synth_hold_queue(rng)` — generates a flow with menu → hold node (random interval/repeat) → softphone, with scenario timeout ≥ total hold duration
- [x] 3.5 Implement `_synth_human_pickup(rng)` — generates a flow where IVR menu transitions to hold then softphone simulating unexpected human pickup; scenario goal instructs agent to respond when human answers
- [x] 3.6 Implement `_synth_dtmf_timeout_loop(rng)` — generates a menu node whose `default` routes back to itself; scenario instructs agent to detect and escape the loop
- [x] 3.7 Implement `_synth_menu_repeat_cap(rng)` — generates a chain of N (random 2–4) menu nodes ending in hangup; scenario instructs agent to navigate before cap is hit
- [x] 3.8 Register all pattern functions in a `PATTERNS: dict[str, Callable]` registry used by `synthesize()`

## 4. Synthesizer Tests

- [x] 4.1 Unit test: same seed produces identical YAML output for each pattern
- [x] 4.2 Unit test: different seeds produce different parameter values for hold-queue pattern
- [x] 4.3 Unit test: all generated flow YAMLs load cleanly via `parse_config()` for each pattern
- [x] 4.4 Unit test: all generated scenario YAMLs are valid (contain required keys: `id`, `description`, `agent`, `timeout`, `success_criteria`)

## 5. CLI — `ivr-synthesize` Command

- [x] 5.1 Add `ivr_synthesize` Click command to `shuo/cli.py` with options `--patterns` (multiple, optional), `--output` (required), `--seed` (int, optional)
- [x] 5.2 Implement command body: call `synthesize()`, write each result's flow YAML to `<output>/flows/<pattern>.yaml` and scenario YAML to `<output>/scenarios/<pattern>.yaml`, creating dirs as needed
- [x] 5.3 Register `ivr_synthesize` in the `cli` group so `voice-agent ivr-synthesize` is available
- [x] 5.4 Verify `voice-agent --help` lists `ivr-synthesize` and `voice-agent ivr-synthesize --help` shows `--patterns`, `--output`, `--seed`

## 6. CLI — `bench --synthesize` Flag

- [x] 6.1 Add `--synthesize` boolean flag to the `bench` Click command in `shuo/cli.py`
- [x] 6.2 When `--synthesize` is set, call `synthesize()` into a `tempfile.mkdtemp()` directory and merge the resulting scenario paths with any `--dataset` paths before running the bench
- [x] 6.3 Log the temp dir path and seed used when `--synthesize` is active so results are reproducible

## 7. Pre-built Dataset

- [x] 7.1 Run `voice-agent ivr-synthesize --output eval/ --seed 0` to generate `eval/flows/` and `eval/scenarios/` edge-case files
- [x] 7.2 Rename / consolidate the generated scenario file to `eval/scenarios/synthesized_edge_cases.yaml` (concatenate all pattern scenario entries into one file)
- [x] 7.3 Commit `eval/scenarios/synthesized_edge_cases.yaml` and the generated flow YAMLs under `simulator/flows/synthesized/`
- [x] 7.4 Verify `voice-agent bench --dataset eval/scenarios/synthesized_edge_cases.yaml` runs without errors using the committed flows
