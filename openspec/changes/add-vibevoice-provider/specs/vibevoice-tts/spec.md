## ADDED Requirements

### Requirement: VibeVoice TTS provider is available
The system SHALL include a `VibeVoiceTTS` class in `shuo/voice_vibevoice.py` that implements the TTS provider interface (`start`, `send`, `flush`, `stop`, `cancel`, `bind`) using the VibeVoice-Realtime-0.5B model.

#### Scenario: Provider selected via env var
- **WHEN** `TTS_PROVIDER=vibevoice` is set and `_create_tts()` is called
- **THEN** a `VibeVoiceTTS` instance is returned

#### Scenario: Provider raises on unknown value
- **WHEN** `TTS_PROVIDER=unknown` is set
- **THEN** `_create_tts()` raises `ValueError`

### Requirement: Model loads once at startup
The system SHALL load the VibeVoice model into a module-level singleton on the first call to `VibeVoiceTTS.start()`, and reuse it for all subsequent instances without reloading.

#### Scenario: Singleton reuse
- **WHEN** two `VibeVoiceTTS` instances both call `start()`
- **THEN** the model is only loaded once (same object reference)

#### Scenario: Model path configuration
- **WHEN** `VIBEVOICE_MODEL` env var is set to a local path
- **THEN** the model is loaded from that path instead of the default HuggingFace Hub ID

### Requirement: Streaming text input is supported
The system SHALL accept text in multiple `send()` calls before `flush()`, feeding each chunk to the model's streaming input to minimize first-audio latency.

#### Scenario: Incremental text delivery
- **WHEN** `send("Hello")` and `send(" world")` are called before `flush()`
- **THEN** audio output begins producing chunks without waiting for the full sentence

#### Scenario: Flush signals end of turn
- **WHEN** `flush()` is called
- **THEN** the model finalizes generation and `on_done` is called after the last audio chunk

### Requirement: Audio output is μ-law 8 kHz base64
The system SHALL convert VibeVoice's 24 kHz float32 PCM output to μ-law 8 kHz mono PCM and deliver it as base64-encoded strings to the `on_audio` callback, compatible with Twilio's media stream format.

#### Scenario: Audio chunks delivered to callback
- **WHEN** the model generates audio
- **THEN** `on_audio` is called with base64-encoded μ-law 8 kHz strings

#### Scenario: Chunk duration approximately 20ms
- **WHEN** audio chunks are emitted
- **THEN** each chunk represents approximately 20ms of audio (≈160 samples at 8kHz)

### Requirement: Inference runs off the asyncio event loop
The system SHALL run VibeVoice model inference in a thread executor so that the asyncio event loop is never blocked during synthesis.

#### Scenario: Event loop not blocked
- **WHEN** `send()` is called and inference is running
- **THEN** other asyncio tasks (barge-in detection, audio playback) continue executing concurrently

### Requirement: Cancel stops inference immediately
The system SHALL support `cancel()` to immediately abort any in-progress inference and release resources without waiting for generation to complete.

#### Scenario: Cancel during generation
- **WHEN** `cancel()` is called while audio is being generated
- **THEN** generation stops and `on_done` is NOT called

### Requirement: Device is configurable
The system SHALL read `VIBEVOICE_DEVICE` env var (`cpu`, `cuda`, `mps`) to select the compute device, defaulting to `cpu`.

#### Scenario: GPU device selection
- **WHEN** `VIBEVOICE_DEVICE=cuda` is set
- **THEN** the model and inference tensors are placed on the CUDA device
