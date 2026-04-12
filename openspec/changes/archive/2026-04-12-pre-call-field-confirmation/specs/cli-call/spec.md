## ADDED Requirements

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
