## ADDED Requirements

### Requirement: CallContext schema defines all agent and call fields
The system SHALL define a `CallContext` dataclass in `shuo/context.py` with the following fields:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `goal` | str | YES | — | What the agent is trying to accomplish on this call |
| `agent_name` | str | NO | `"Alex"` | Name the agent introduces itself with |
| `agent_role` | str | NO | `"a professional assistant"` | Role the agent describes itself as |
| `agent_tone` | str | NO | `"friendly and concise"` | Tone/style instruction included in system prompt |
| `caller_name` | str | NO | `None` | Name of the person being called, if known |
| `caller_context` | str | NO | `None` | Known facts about the caller (account status, prior interactions, etc.) |
| `constraints` | list[str] | NO | `[]` | Things the agent must or must not do (e.g., "never offer refunds over $50") |
| `success_criteria` | str | NO | `None` | How the agent knows the call succeeded |

#### Scenario: Required field missing raises error
- **WHEN** a `CallContext` is constructed without a `goal`
- **THEN** a `ValueError` is raised with a message indicating `goal` is required

#### Scenario: Optional fields use defaults
- **WHEN** a `CallContext` is constructed with only `goal` provided
- **THEN** `agent_name` equals `"Alex"`, `agent_role` equals `"a professional assistant"`, `agent_tone` equals `"friendly and concise"`, `constraints` equals `[]`, and all other optional fields are `None`

### Requirement: CallContext serializes to and from YAML
The system SHALL support `CallContext.from_yaml(path)` and `CallContext.to_yaml(path)` for file-based persistence.

#### Scenario: Round-trip YAML serialization
- **WHEN** a fully populated `CallContext` is serialized to YAML and then deserialized
- **THEN** the resulting object equals the original

#### Scenario: YAML with missing optional fields
- **WHEN** a YAML file contains only `goal`
- **THEN** deserialization succeeds and all other fields use their defaults

### Requirement: build_system_prompt assembles the agent's system prompt from CallContext
The system SHALL provide `build_system_prompt(ctx: CallContext) -> str` in `shuo/context.py`. The returned string SHALL include:
- The agent's name and role
- The agent's goal for this call
- Caller name and context if provided
- All constraints as explicit instructions
- Success criteria if provided
- The agent's tone instruction

No field in the system prompt SHALL be fabricated or inferred beyond the provided `CallContext` values and their defaults.

#### Scenario: Full context produces complete prompt
- **WHEN** `build_system_prompt` is called with all fields populated
- **THEN** the returned string contains the agent name, role, goal, caller name, caller context, each constraint, and success criteria

#### Scenario: Minimal context produces valid prompt
- **WHEN** `build_system_prompt` is called with only `goal` provided
- **THEN** the returned string contains "Alex", "a professional assistant", and the goal — and contains no placeholder text like "[UNKNOWN]" or "N/A"
