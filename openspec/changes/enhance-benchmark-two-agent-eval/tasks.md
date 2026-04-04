## 1. Data Model

- [x] 1.1 Add `TwoAgentScenarioConfig`, `TwoAgentSuccessCriteria`, `TwoAgentCriteriaResult`, `TwoAgentScenarioResult` dataclasses to `shuo/shuo/bench.py`
- [x] 1.2 Implement `load_two_agent_scenarios(path)` that parses the two-agent YAML schema into `TwoAgentScenarioConfig` objects and raises `ValueError` on missing required fields

## 2. Text Bridge

- [x] 2.1 Implement `TwoAgentBridge` class in `shuo/shuo/bench.py` that holds refs to two `BenchISP` instances and cross-injects each agent's observer transcript into the peer's `_inject` as `FluxEndOfTurnEvent`
- [x] 2.2 Add turn counting to `TwoAgentBridge`; expose `total_turns` and `bilateral_transcript` (list of `{"role": ..., "text": ...}` dicts) 
- [x] 2.3 Add ready-wait logic: poll both `bench_isp_caller._inject` and `bench_isp_answerer._inject` (up to 0.5s) before firing the synthetic `[call connected]` event

## 3. Scenario Runner

- [x] 3.1 Implement `run_two_agent_scenario(scenario, ...)` that creates two paired `BenchISP` + `_BenchFluxPool` + `_BenchTTSPool` instances, starts both `run_conversation` tasks, runs `TwoAgentBridge`, and handles termination (hangup / max_turns / timeout)
- [x] 3.2 Implement `evaluate_two_agent_criteria(criteria, bilateral_transcript, turns)` → `TwoAgentCriteriaResult`
- [x] 3.3 Implement `run_two_agent_benchmark(dataset_path, summary_path)` that loads scenarios, runs each with `run_two_agent_scenario`, prints a terminal table, and delegates to reporting

## 4. Reporting

- [x] 4.1 Implement `write_run_reports(results, dataset_path)` that creates `reports/` if needed and writes `<stem>_<timestamp>.json` and `<stem>_<timestamp>.md`
- [x] 4.2 Implement `append_summary(results, dataset_path, summary_path)` that creates or appends a summary block to the shared Markdown summary file with timestamp, dataset name, pass rate, and per-difficulty breakdown
- [x] 4.3 Implement `print_two_agent_metrics_report(results)` terminal table with columns: Scenario ID, Difficulty, Result, Turns, Latency

## 5. CLI

- [x] 5.1 Add `--mode` option (`ivr` | `two-agent`, default `ivr`) to the `bench` Click command in `shuo/shuo/cli.py`
- [x] 5.2 Add `--summary` option (default `reports/bench_summary.md`) to the `bench` Click command
- [x] 5.3 Wire `--mode two-agent` to call `run_two_agent_benchmark(dataset, summary_path=summary)`

## 6. Scenario YAML Files

- [x] 6.1 Create `scenarios/two_agent_easy.yaml` with 4+ easy scenarios (no verification required, ≤6 turns, varied service domains)
- [x] 6.2 Create `scenarios/two_agent_medium.yaml` with 4+ medium scenarios (1-2 verification fields, ≤12 turns)
- [x] 6.3 Create `scenarios/two_agent_hard.yaml` with 4+ hard scenarios (multi-field verification, partial/misleading info, ≤20 turns)

## 7. Infrastructure

- [x] 7.1 Create `reports/.gitkeep` and add `reports/*.json` and `reports/*.md` to `.gitignore` so report files are not committed
