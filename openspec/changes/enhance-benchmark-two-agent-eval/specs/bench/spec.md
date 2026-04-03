## ADDED Requirements

### Requirement: bench CLI --mode flag
The `voice-agent bench` command SHALL accept a `--mode` flag with values `ivr` (default) and `two-agent`. When `--mode ivr`, existing IVR benchmark behavior is unchanged.

#### Scenario: Default mode is ivr
- **WHEN** `voice-agent bench --dataset scenarios/example_ivr.yaml` is run without `--mode`
- **THEN** the IVR benchmark runner executes (same behavior as before this change)

#### Scenario: Two-agent mode dispatches to new runner
- **WHEN** `voice-agent bench --mode two-agent --dataset scenarios/two_agent_easy.yaml` is run
- **THEN** `run_two_agent_benchmark` is called with the dataset path

### Requirement: bench CLI --summary flag
The `voice-agent bench` command SHALL accept a `--summary <path>` flag specifying where the shared cumulative summary Markdown file is written/appended.

#### Scenario: Default summary path
- **WHEN** `--summary` is not specified
- **THEN** the summary is written to `reports/bench_summary.md`

#### Scenario: Custom summary path
- **WHEN** `--summary /tmp/custom.md` is specified
- **THEN** the summary is written to `/tmp/custom.md`
