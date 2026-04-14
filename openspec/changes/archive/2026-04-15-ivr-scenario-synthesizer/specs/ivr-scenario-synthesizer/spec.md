## ADDED Requirements

### Requirement: Synthesizer generates valid IVR flow YAML for each edge-case pattern
The synthesizer SHALL produce a syntactically valid IVR flow YAML for each registered edge-case pattern. Each generated flow MUST be loadable by `simulator/config.py:parse_config()` without errors.

#### Scenario: Out-of-hours pattern generates a complete flow
- **WHEN** `synthesize(patterns=["out-of-hours"])` is called
- **THEN** the returned flow YAML contains a start node that leads to an `out-of-hours` typed node followed by a `hangup` node

#### Scenario: Hold queue pattern generates a flow with a hold node
- **WHEN** `synthesize(patterns=["hold-queue"])` is called
- **THEN** the returned flow YAML contains a `hold` node with `repeat` and `interval` fields set to the parameterized values

#### Scenario: Human pickup pattern generates a flow that transitions from IVR menu to human speech
- **WHEN** `synthesize(patterns=["human-pickup"])` is called
- **THEN** the returned flow YAML contains a `menu` node followed by a `hold` node followed by a `softphone` node representing unexpected human pickup

#### Scenario: DTMF timeout loop pattern generates a looping menu with no exit
- **WHEN** `synthesize(patterns=["dtmf-timeout-loop"])` is called
- **THEN** the returned flow YAML contains a `menu` node whose `default` route points back to itself, simulating a stuck IVR

#### Scenario: Menu cap pattern generates a flow that hangs up after N repeats
- **WHEN** `synthesize(patterns=["menu-repeat-cap"])` is called
- **THEN** the returned flow YAML contains a sequence of menu nodes that terminates with a `hangup` node after the configured repeat count

### Requirement: Synthesizer generates matching benchmark scenario YAML for each flow
The synthesizer SHALL produce a benchmark scenario YAML alongside each flow. The scenario MUST reference a success criterion appropriate to the edge-case pattern.

#### Scenario: Out-of-hours scenario expects agent to detect closed state
- **WHEN** `synthesize(patterns=["out-of-hours"])` is called
- **THEN** the scenario YAML contains a `transcript_contains` criterion referencing the out-of-hours message text

#### Scenario: Hold queue scenario expects agent to survive the wait
- **WHEN** `synthesize(patterns=["hold-queue"])` is called
- **THEN** the scenario YAML has a `timeout` at least as large as the total hold duration plus a buffer, and a `max_turns` permissive enough for hold-loop turns

#### Scenario: Human pickup scenario expects agent to respond after human speaks
- **WHEN** `synthesize(patterns=["human-pickup"])` is called
- **THEN** the scenario YAML sets a goal that instructs the agent to speak once a human answers

### Requirement: Synthesizer accepts a seed for reproducible randomization
The synthesizer SHALL accept an optional integer `seed` parameter. When provided, all random parameter choices (durations, messages, queue depths) SHALL be deterministic for that seed.

#### Scenario: Same seed produces identical output
- **WHEN** `synthesize(patterns=["hold-queue"], seed=42)` is called twice
- **THEN** both calls return byte-identical YAML strings

#### Scenario: Different seeds produce different parameter values
- **WHEN** `synthesize(patterns=["hold-queue"], seed=1)` and `synthesize(patterns=["hold-queue"], seed=2)` are called
- **THEN** at least one parameter value (e.g. hold duration) differs between the two outputs

### Requirement: Simulator engine renders `hold` node type as TwiML
The simulator engine SHALL render a `hold` node as a `<Pause>` TwiML element whose `length` equals the node's configured `interval`, repeated `repeat` times via `<Redirect>`, producing a looping hold experience without external audio dependencies.

#### Scenario: Hold node renders correct TwiML pause length
- **WHEN** a `hold` node with `interval: 10` and `repeat: 3` is rendered
- **THEN** the TwiML contains `<Pause length="10"/>` and a redirect back to the same node for the remaining repeats, finally redirecting to `next`

#### Scenario: Hold node with repeat exhausted redirects to next node
- **WHEN** the hold loop counter reaches zero
- **THEN** the engine redirects to the node's `next` field

### Requirement: Simulator engine renders `out-of-hours` node type as TwiML
The simulator engine SHALL render an `out-of-hours` node as a `<Say>` with the node's message followed by `<Hangup/>`.

#### Scenario: Out-of-hours node renders say then hangup
- **WHEN** an `out-of-hours` node with `say: "We are currently closed."` is rendered
- **THEN** the TwiML contains `<Say>We are currently closed.</Say><Hangup/>`

### Requirement: `ivr-synthesize` CLI command writes flow and scenario files to disk
The `voice-agent ivr-synthesize` command SHALL accept `--patterns` (comma-separated or repeatable), `--output <dir>`, and `--seed <int>`, generate flows and scenarios for each pattern, and write them to `<output>/flows/` and `<output>/scenarios/` respectively.

#### Scenario: CLI writes files for all specified patterns
- **WHEN** `voice-agent ivr-synthesize --patterns out-of-hours,hold-queue --output /tmp/bench`
- **THEN** files `/tmp/bench/flows/out-of-hours.yaml` and `/tmp/bench/scenarios/out-of-hours.yaml` (and equivalents for hold-queue) are created

#### Scenario: CLI with no --patterns generates all registered patterns
- **WHEN** `voice-agent ivr-synthesize --output /tmp/bench` is run with no `--patterns`
- **THEN** one flow + one scenario file is written for every registered pattern

### Requirement: `voice-agent bench` accepts `--synthesize` flag
The `bench` command SHALL accept `--synthesize` (flag, no value). When set, it SHALL auto-generate all edge-case flows and scenarios into a temp directory and include them in the benchmark run alongside any `--dataset` file specified.

#### Scenario: --synthesize adds synthesized scenarios to bench run
- **WHEN** `voice-agent bench --dataset eval/scenarios/example_ivr.yaml --synthesize` is run
- **THEN** the bench runs scenarios from both the dataset file and the synthesized edge-case flows

#### Scenario: --synthesize without --dataset runs only synthesized scenarios
- **WHEN** `voice-agent bench --synthesize` is run with no `--dataset`
- **THEN** the bench runs only the synthesized edge-case scenarios

### Requirement: Pre-built edge-case scenario dataset is committed to the repo
A file `eval/scenarios/synthesized_edge_cases.yaml` SHALL be committed containing pre-generated scenarios for all registered patterns (generated with a fixed seed for reproducibility). This file SHALL be usable with `voice-agent bench --dataset` without invoking synthesis at runtime.

#### Scenario: Pre-built dataset loads without synthesis
- **WHEN** `voice-agent bench --dataset eval/scenarios/synthesized_edge_cases.yaml` is run
- **THEN** the bench runs all edge-case scenarios without invoking the synthesizer
