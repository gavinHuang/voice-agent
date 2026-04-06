## ADDED Requirements

### Requirement: Per-run JSON report
The system SHALL write a JSON report file after each benchmark run to `reports/<dataset_stem>_<timestamp>.json` (e.g., `reports/two_agent_easy_20260404T153000.json`).

#### Scenario: JSON report is created after run
- **WHEN** `run_two_agent_benchmark("scenarios/two_agent_easy.yaml")` completes
- **THEN** a file matching `reports/two_agent_easy_*.json` exists on disk

#### Scenario: JSON report contains per-scenario results
- **WHEN** the JSON report is parsed
- **THEN** it contains an array of objects each with: `scenario_id`, `difficulty`, `passed`, `turns`, `wall_clock_s`, `error`, `criteria`, and `bilateral_transcript`

#### Scenario: JSON report criteria object structure
- **WHEN** reading the `criteria` field of a scenario result in the JSON report
- **THEN** it contains `goal_phrases_pass`, `verification_pass`, `turns_pass`, and `passed` booleans

### Requirement: Per-run Markdown report
The system SHALL write a human-readable Markdown report file to `reports/<dataset_stem>_<timestamp>.md` alongside the JSON report.

#### Scenario: Markdown report contains summary table
- **WHEN** the Markdown report is opened
- **THEN** it contains a table with columns: Scenario ID, Difficulty, Result, Turns, Latency, and one row per scenario

#### Scenario: Markdown report contains aggregate summary
- **WHEN** the Markdown report is opened
- **THEN** it contains a summary section with: total pass rate, pass rate per difficulty tier, total scenarios, average turns, and average latency

### Requirement: Cumulative shared summary report
The system SHALL append a run summary block to a shared Markdown file (default `reports/bench_summary.md`) after each run. The file is created if it does not exist; if it exists, the new run is appended.

#### Scenario: Summary file created on first run
- **WHEN** `bench_summary.md` does not exist and a run completes
- **THEN** `bench_summary.md` is created with a header and the first run's summary block

#### Scenario: Summary file appended on subsequent runs
- **WHEN** `bench_summary.md` already contains prior run summaries
- **THEN** after a new run, the file contains all prior entries plus a new run block at the end

#### Scenario: Each summary block contains run metadata
- **WHEN** reading a summary block in `bench_summary.md`
- **THEN** it contains: run timestamp, dataset file name, total pass rate, per-difficulty breakdown (easy/medium/hard pass counts), and a link to the per-run report files

### Requirement: Reports directory auto-creation
The system SHALL create the `reports/` directory if it does not exist when writing a report.

#### Scenario: Reports directory created automatically
- **WHEN** the `reports/` directory does not exist and a benchmark run completes
- **THEN** the `reports/` directory is created and report files are written successfully

### Requirement: Custom summary path via CLI flag
The system SHALL accept a `--summary <path>` CLI flag for `voice-agent bench` to specify the shared summary file location.

#### Scenario: Custom summary path used when specified
- **WHEN** `voice-agent bench --mode two-agent --dataset scenarios/two_agent_easy.yaml --summary /tmp/my_summary.md` is run
- **THEN** the run summary is appended to `/tmp/my_summary.md` instead of `reports/bench_summary.md`
