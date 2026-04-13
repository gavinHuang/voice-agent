## MODIFIED Requirements

### Requirement: CallContext is a validated data model
`CallContext` in `shuo/context.py` SHALL be implemented as a Pydantic `BaseModel` (not a plain dataclass). All existing fields — `goal`, `agent_name`, `agent_role`, `agent_tone`, `agent_background`, `caller_name`, `constraints`, `success_criteria` — SHALL be preserved with the same types and defaults. The `build_system_prompt(ctx)` function SHALL work with the Pydantic model without modification.

#### Scenario: CallContext serialises to JSON
- **WHEN** a `CallContext` instance is created programmatically
- **THEN** `ctx.model_dump_json()` returns valid JSON and `CallContext.model_validate_json(json_str)` round-trips without data loss

#### Scenario: Invalid field type raises validation error
- **WHEN** `CallContext(goal=123)` is called with a non-string goal
- **THEN** Pydantic raises a `ValidationError` describing the type mismatch

#### Scenario: build_system_prompt works unchanged
- **WHEN** `build_system_prompt(ctx)` is called with a Pydantic `CallContext`
- **THEN** the returned string is identical to what a plain-dataclass `CallContext` would have produced for the same field values

#### Scenario: YAML loading still works
- **WHEN** `CallContext` is loaded from a YAML file via the CLI
- **THEN** the loaded object is a valid Pydantic `CallContext` and `build_system_prompt()` runs without error
