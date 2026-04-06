## ADDED Requirements

### Requirement: Answerer agent YAML configuration
The system SHALL configure the answerer agent entirely via a `goal` string and optional `opening_line` string in the scenario YAML, without requiring a new agent implementation.

#### Scenario: Answerer goal defines service scope and verification policy
- **WHEN** a scenario YAML sets `answerer.goal` to "You are a bank support agent. You MUST verify the caller's account number and full name before discussing account details."
- **THEN** the answerer agent is initialised with that exact string as its `get_goal` return value

#### Scenario: Answerer with opening line speaks first
- **WHEN** `answerer.opening_line` is set to "Thank you for calling Acme Bank. How can I help you today?"
- **THEN** after connection, the bridge injects that opening line into the caller's `_inject` before the caller speaks, and the caller receives `FluxEndOfTurnEvent(transcript="Thank you for calling Acme Bank. How can I help you today?")`

#### Scenario: Answerer without opening line waits for caller
- **WHEN** `answerer.opening_line` is absent or empty
- **THEN** the caller receives the synthetic `[call connected]` event first and the answerer waits for the first caller speech before responding

### Requirement: Answerer verification gating via goal prompt
The system SHALL use the answerer's LLM goal prompt to control verification gating — the answerer only provides requested information after the caller supplies all required verification fields.

#### Scenario: Answerer refuses without verification
- **WHEN** the caller asks "What is my account balance?" without providing an account number or name
- **THEN** the answerer's response (as captured in the bilateral transcript) contains a refusal or request for verification (e.g., "please provide your account number")

#### Scenario: Answerer provides info after successful verification
- **WHEN** the caller provides a valid account number and full name matching the scenario's context
- **THEN** the answerer responds with the requested information

### Requirement: Answerer knowledge scope from goal context
The system SHALL allow scenario authors to embed service knowledge (account details, product info, policies) directly in the `answerer.goal` string, making the answerer's knowledge fully configurable per scenario.

#### Scenario: Answerer has embedded knowledge
- **WHEN** `answerer.goal` includes "Account 12345 belongs to John Smith with a balance of $4,200"
- **THEN** the answerer can accurately respond to balance queries after the caller verifies as John Smith with account 12345
