## ADDED Requirements

### Requirement: `voice-agent` exposes `ivr-synthesize` sub-command
The CLI SHALL register an `ivr-synthesize` sub-command under the `voice-agent` entry point. This command is independent of the `call` and `bench` commands.

#### Scenario: ivr-synthesize sub-command is listed in help
- **WHEN** `voice-agent --help` is run
- **THEN** `ivr-synthesize` appears in the list of available commands

#### Scenario: ivr-synthesize --help shows expected options
- **WHEN** `voice-agent ivr-synthesize --help` is run
- **THEN** the output documents `--patterns`, `--output`, and `--seed` options
