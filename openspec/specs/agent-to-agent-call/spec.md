## ADDED Requirements

### Requirement: AgentPhone enables in-process agent-to-agent calls
The system SHALL provide `AgentPhone` in `shuo/phone.py` with a `pair()` static method that returns two `Phone` instances wired to each other via `LocalPhone.pair()`. Each phone in the pair SHALL support the full `Phone` protocol so that a standard `run_call()` coroutine can be passed either one.

#### Scenario: Two agents converse locally
- **WHEN** `AgentPhone.pair()` is called and two `run_call()` coroutines are started concurrently with the returned phones
- **THEN** audio produced by the first agent is received by the second agent's STT pipeline, and vice versa, with no Twilio or network involvement

### Requirement: Agent-to-agent calls are scoped to a tenant
Both legs of an agent-to-agent call SHALL carry the same `tenant_id`. The call registry SHALL record both call legs under that tenant. Neither leg SHALL be visible to other tenants.

#### Scenario: Two-agent call appears in registry under correct tenant
- **WHEN** an agent-to-agent call is initiated for `tenant_id = "acme"`
- **THEN** both call legs appear in the registry filtered by `"acme"` and neither appears under any other tenant

### Requirement: LocalPhone connection timeout
`LocalPhone` paired instances SHALL raise a `TimeoutError` if the remote end does not connect within 5 seconds of the first audio packet being sent. This prevents hung coroutines when one agent crashes before connecting.

#### Scenario: One agent crashes before connecting
- **WHEN** one leg of a `LocalPhone.pair()` starts sending but the other coroutine raises an exception before consuming audio
- **THEN** the sending leg raises `TimeoutError` within 5 seconds and does not hang indefinitely

### Requirement: Two-agent benchmark scenario
The simulator SHALL include a YAML scenario (`simulator/flows/two_agent.yaml`) that exercises a two-agent local call. The benchmark MUST complete without errors and be runnable via `voice-agent bench --dataset eval/scenarios/two_agent.yaml`.

#### Scenario: Two-agent benchmark runs to completion
- **WHEN** `voice-agent bench --dataset eval/scenarios/two_agent.yaml` is executed
- **THEN** the benchmark completes with no failed turns and produces a latency report
