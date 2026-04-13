## ADDED Requirements

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
