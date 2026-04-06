## ADDED Requirements

### Requirement: Two-agent scenario loading
The system SHALL load two-agent scenarios from a YAML file with a top-level `scenarios:` list where each scenario contains `caller`, `answerer`, `difficulty`, `timeout`, and `success_criteria` fields.

#### Scenario: Load valid two-agent YAML
- **WHEN** `load_two_agent_scenarios("scenarios/two_agent_easy.yaml")` is called with a valid file
- **THEN** it returns a list of `TwoAgentScenarioConfig` objects with populated `caller`, `answerer`, and `success_criteria` fields

#### Scenario: Missing required id field raises ValueError
- **WHEN** a scenario entry is missing the `id` field
- **THEN** `load_two_agent_scenarios` raises `ValueError` naming the missing field

### Requirement: Two-agent conversation orchestration
The system SHALL run two simultaneous `run_conversation` instances (caller and answerer) connected via paired `LocalISP` loopbacks, bridging each agent's transcript output into the peer's `FluxEndOfTurnEvent` injection point.

#### Scenario: Caller speaks first after connection
- **WHEN** a two-agent scenario starts
- **THEN** the caller receives a synthetic `FluxEndOfTurnEvent(transcript="[call connected]")` to trigger its opening line before the answerer speaks

#### Scenario: Answerer receives caller speech as turn event
- **WHEN** the caller agent's observer emits `{"type": "transcript", "text": "Hello, I need help with my account"}` 
- **THEN** the bridge injects `FluxEndOfTurnEvent(transcript="Hello, I need help with my account")` into the answerer's `_inject`

#### Scenario: Caller receives answerer speech as turn event
- **WHEN** the answerer agent's observer emits `{"type": "transcript", "text": "Please provide your account number"}`
- **THEN** the bridge injects `FluxEndOfTurnEvent(transcript="Please provide your account number")` into the caller's `_inject`

#### Scenario: Conversation terminates on hangup
- **WHEN** either agent calls `hangup()` on its ISP
- **THEN** both agent tasks are cancelled and the scenario result is captured

#### Scenario: Conversation terminates on max turns exceeded
- **WHEN** total turn count reaches `max_turns` from the scenario config
- **THEN** the orchestrator cancels both tasks and records a `max_turns_exceeded` error

#### Scenario: Conversation terminates on wall-clock timeout
- **WHEN** wall-clock time exceeds `timeout` seconds from scenario config
- **THEN** the orchestrator cancels both tasks and records a `timeout` error

### Requirement: Bilateral transcript capture
The system SHALL capture all speech from both agents into a unified ordered transcript list of `{"role": "caller"|"answerer", "text": str}` records.

#### Scenario: Transcript records agent role
- **WHEN** the caller says "I need to check my balance" and answerer says "Please verify your identity"
- **THEN** the bilateral transcript contains `[{"role": "caller", "text": "I need to check my balance"}, {"role": "answerer", "text": "Please verify your identity"}]` in order

### Requirement: Two-agent success criteria evaluation
The system SHALL evaluate `TwoAgentSuccessCriteria` against the bilateral transcript, turn count, and verification status.

#### Scenario: Goal completion check passes when all phrases present
- **WHEN** `goal_phrases` are `["balance confirmed", "thank you"]` and both appear in the combined transcript
- **THEN** `goal_phrases_pass` is `True`

#### Scenario: Goal completion check fails when phrase missing
- **WHEN** `goal_phrases` are `["balance confirmed"]` and no transcript entry contains it
- **THEN** `goal_phrases_pass` is `False`

#### Scenario: Verification check passes when answerer confirms
- **WHEN** `require_verification_confirmed` is `True` and the answerer's transcript contains any phrase from `verification_phrases`
- **THEN** `verification_pass` is `True`

#### Scenario: Turn limit check
- **WHEN** total turns exceed `max_turns`
- **THEN** `turns_pass` is `False`

#### Scenario: Overall pass requires all criteria to pass
- **WHEN** any individual criterion fails
- **THEN** `passed` is `False`

### Requirement: No audio I/O in two-agent benchmark
The system SHALL use no-op Flux and TTS pool stubs for both agents — no Deepgram, ElevenLabs, or audio codec calls are made during a two-agent benchmark run.

#### Scenario: No external API calls during run
- **WHEN** a two-agent scenario runs without API keys set
- **THEN** the scenario completes without raising authentication or network errors (aside from the LLM call to Groq which is still required)
