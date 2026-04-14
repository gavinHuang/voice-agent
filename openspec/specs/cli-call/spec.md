# cli-call Specification

## Purpose
TBD - created by archiving change pre-call-field-confirmation. Update Purpose after archive.
## Requirements
### Requirement: `voice-agent call` accepts a --context flag for YAML context files
The `call` command SHALL accept `--context <path>` pointing to a YAML file that provides `CallContext` field values. Fields in the YAML file serve as the base; CLI flags override individual fields.

#### Scenario: Context file loads successfully
- **WHEN** `--context path/to/ctx.yaml` is passed and the file is valid
- **THEN** all fields from the YAML are loaded into `CallContext` before confirmation

#### Scenario: CLI flag overrides context file field
- **WHEN** both `--context ctx.yaml` (with `goal: "A"`) and `--goal "B"` are passed
- **THEN** the effective `goal` is `"B"`

#### Scenario: Missing context file exits with error
- **WHEN** `--context nonexistent.yaml` is passed
- **THEN** the command exits with an error message before attempting to dial

### Requirement: `voice-agent call` accepts per-field flags for CallContext
The `call` command SHALL accept individual flags for each `CallContext` field:
- `--goal TEXT` (already exists; behavior unchanged)
- `--agent-name TEXT`
- `--agent-role TEXT`
- `--agent-tone TEXT`
- `--caller-name TEXT`
- `--caller-context TEXT`
- `--constraint TEXT` (repeatable, appends to `constraints` list)
- `--success-criteria TEXT`

#### Scenario: Per-field flags populate CallContext
- **WHEN** `--agent-name "Jordan" --caller-name "Sam"` are passed
- **THEN** `CallContext.agent_name` is `"Jordan"` and `CallContext.caller_name` is `"Sam"`

#### Scenario: --constraint flag is repeatable
- **WHEN** `--constraint "never offer refunds" --constraint "speak only English"` are passed
- **THEN** `CallContext.constraints` is `["never offer refunds", "speak only English"]`

### Requirement: `voice-agent` exposes `ivr-synthesize` sub-command
The CLI SHALL register an `ivr-synthesize` sub-command under the `voice-agent` entry point. This command is independent of the `call` and `bench` commands.

#### Scenario: ivr-synthesize sub-command is listed in help
- **WHEN** `voice-agent --help` is run
- **THEN** `ivr-synthesize` appears in the list of available commands

#### Scenario: ivr-synthesize --help shows expected options
- **WHEN** `voice-agent ivr-synthesize --help` is run
- **THEN** the output documents `--patterns`, `--output`, and `--seed` options

### Requirement: `voice-agent` commands accept `--tts-provider` flag
The `voice-agent serve` and `voice-agent call` commands SHALL accept a `--tts-provider` flag accepting values `kokoro`, `fish`, `elevenlabs`, and `vibevoice`, overriding the `TTS_PROVIDER` environment variable for that invocation.

#### Scenario: vibevoice selected via flag
- **WHEN** `voice-agent serve --tts-provider vibevoice` is run
- **THEN** the server starts with `TTS_PROVIDER=vibevoice` regardless of any env var setting

#### Scenario: Invalid provider value rejected
- **WHEN** `voice-agent serve --tts-provider unknown_provider` is run
- **THEN** the command exits with an error listing valid choices

### Requirement: `TTS_PROVIDER=vibevoice` is documented in .env.example
The `.env.example` file SHALL include `vibevoice` as a commented option for `TTS_PROVIDER`, alongside `VIBEVOICE_MODEL` and `VIBEVOICE_DEVICE` env vars.

#### Scenario: .env.example shows vibevoice options
- **WHEN** a user opens `.env.example`
- **THEN** they can see `TTS_PROVIDER=vibevoice`, `VIBEVOICE_MODEL`, and `VIBEVOICE_DEVICE` as documented options

