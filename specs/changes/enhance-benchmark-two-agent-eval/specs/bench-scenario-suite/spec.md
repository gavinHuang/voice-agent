## ADDED Requirements

### Requirement: Difficulty tiers in scenario YAML
The system SHALL support a `difficulty` field on each scenario with values `easy`, `medium`, or `hard`. The runner SHALL record difficulty in reports but apply no different execution logic.

#### Scenario: Difficulty field is loaded and stored
- **WHEN** a scenario YAML entry has `difficulty: medium`
- **THEN** the loaded `TwoAgentScenarioConfig.difficulty` equals `"medium"`

#### Scenario: Missing difficulty field defaults to medium
- **WHEN** a scenario YAML entry omits `difficulty`
- **THEN** `TwoAgentScenarioConfig.difficulty` defaults to `"medium"`

### Requirement: Easy scenario suite
The system SHALL ship a `scenarios/two_agent_easy.yaml` file with at least four scenarios covering direct single-topic requests that require no verification.

#### Scenario: Easy scenarios require no verification
- **WHEN** running any easy scenario
- **THEN** the caller can obtain the requested information without providing identity verification fields

#### Scenario: Easy scenarios complete within low turn budget
- **WHEN** running any easy scenario with a capable LLM
- **THEN** the scenario completes within 6 turns

### Requirement: Medium scenario suite
The system SHALL ship a `scenarios/two_agent_medium.yaml` file with at least four scenarios where the caller must provide one or two verification fields (e.g., account number OR name) before the answerer helps.

#### Scenario: Medium scenarios require partial verification
- **WHEN** the caller provides one of two required verification fields
- **THEN** the answerer requests the missing field before proceeding

#### Scenario: Medium scenarios test caller goal completion
- **WHEN** the caller provides all required fields
- **THEN** the caller obtains the goal information within 12 turns

### Requirement: Hard scenario suite
The system SHALL ship a `scenarios/two_agent_hard.yaml` file with at least four scenarios that combine multi-step verification, partial or misleading caller information, topic constraints, and escalation paths.

#### Scenario: Hard scenarios require multi-field verification
- **WHEN** the answerer requires account number, full name, AND date of birth
- **THEN** the caller must supply all three fields in sequence (possibly across multiple turns) to succeed

#### Scenario: Hard scenarios test partial-information recovery
- **WHEN** the caller initially provides incorrect information for one field
- **THEN** the answerer requests correction and the caller must successfully re-supply it to proceed

#### Scenario: Hard scenarios have strict turn budgets
- **WHEN** running a hard scenario
- **THEN** success criteria require completion within 20 turns

### Requirement: Scenario YAML schema for two-agent mode
Each scenario in a two-agent YAML file SHALL contain the following fields:
- `id` (string, required): unique kebab-case identifier
- `description` (string, required): human-readable description
- `difficulty` (string, optional, default `medium`): `easy`, `medium`, or `hard`
- `timeout` (int, optional, default 120): wall-clock timeout in seconds
- `caller.goal` (string, required): caller agent goal and constraints
- `caller.identity` (string, optional): caller persona prefix
- `caller.context` (string, optional): additional context given to caller (e.g., account number to provide)
- `answerer.goal` (string, required): answerer service scope, knowledge, and verification policy
- `answerer.opening_line` (string, optional): greeting spoken before caller's first turn
- `success_criteria.goal_phrases` (list[str], optional): strings that must appear in the combined transcript for the scenario to pass
- `success_criteria.verification_phrases` (list[str], optional): strings in answerer speech indicating verification was completed
- `success_criteria.require_verification_confirmed` (bool, optional, default false): if true, verification_pass must be true for overall pass
- `success_criteria.max_turns` (int, optional): maximum total turns before fail

#### Scenario: Valid two-agent YAML loads without error
- **WHEN** a YAML file with all required fields is passed to `load_two_agent_scenarios`
- **THEN** all scenarios load successfully with no exceptions

#### Scenario: Scenario without caller.goal raises ValueError
- **WHEN** a scenario is missing `caller.goal`
- **THEN** `load_two_agent_scenarios` raises `ValueError` describing the missing field
