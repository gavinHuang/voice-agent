## ADDED Requirements

### Requirement: Inbound transcript translation
After STT produces a transcript, the system SHALL translate it from the configured source language to the agent's target language before passing it to the LLM. When source and target language are the same (or translation is unconfigured), the transcript SHALL be passed through unchanged.

#### Scenario: Configured translation translates transcript
- **WHEN** `TRANSLATION_SOURCE_LANG=es` and `TRANSLATION_TARGET_LANG=en` are set and the caller says something in Spanish
- **THEN** the LLM receives an English translation of the transcript, not the original Spanish

#### Scenario: No translation when unconfigured
- **WHEN** neither `TRANSLATION_SOURCE_LANG` nor `TRANSLATION_TARGET_LANG` is set
- **THEN** the raw STT transcript is passed directly to the LLM with no extra latency

#### Scenario: Same source and target language is a no-op
- **WHEN** source and target language are both `en`
- **THEN** no translation call is made and the transcript is passed through unchanged

### Requirement: Outbound response translation
Before synthesizing TTS audio, the system SHALL translate the complete LLM response from the agent's target language back to the caller's source language. Control tokens (e.g., `[HOLD_CONTINUE]`, `[HOLD_END]`, `[HANGUP]`) SHALL be preserved and not passed through the translation step.

#### Scenario: LLM response translated before TTS
- **WHEN** `TRANSLATION_SOURCE_LANG=es` and `TRANSLATION_TARGET_LANG=en` are set and the LLM generates an English response
- **THEN** TTS receives the Spanish translation and the caller hears Spanish audio

#### Scenario: Control tokens bypass translation
- **WHEN** the LLM output contains `[HANGUP]` or `[HOLD_CONTINUE]`
- **THEN** those tokens are extracted before translation and the translated text does not alter their meaning or presence

### Requirement: Language configuration via environment variables
The system SHALL read `TRANSLATION_SOURCE_LANG` and `TRANSLATION_TARGET_LANG` from environment variables at startup. Both MUST be set together; setting only one SHALL be treated as misconfiguration and the system SHALL log a warning and disable translation.

#### Scenario: Valid configuration enables translation
- **WHEN** both `TRANSLATION_SOURCE_LANG=fr` and `TRANSLATION_TARGET_LANG=en` are present in the environment
- **THEN** translation is active for all calls on that instance

#### Scenario: Partial configuration disables translation with warning
- **WHEN** only `TRANSLATION_SOURCE_LANG=fr` is set without `TRANSLATION_TARGET_LANG`
- **THEN** translation is disabled and a startup warning is logged

### Requirement: Per-call language override
The system SHALL allow the source and target language to be overridden at call-initiation time via CLI flags (`--source-lang`, `--target-lang`) or programmatic kwargs, taking precedence over environment-variable defaults.

#### Scenario: CLI flag overrides env var
- **WHEN** `TRANSLATION_TARGET_LANG=en` is set but `voice-agent call` is invoked with `--target-lang=de`
- **THEN** the call uses German as the target language

#### Scenario: Outbound call with per-call language
- **WHEN** `voice-agent call +1234567890 --source-lang=ja --target-lang=en` is executed
- **THEN** the agent translates Japanese STT output to English for the LLM and translates English LLM output to Japanese for TTS

### Requirement: Translator abstraction supports multiple providers
The system SHALL expose a `Translator` interface with an async `translate(text, source_lang, target_lang) -> str` method. The default implementation SHALL use the Groq LLM. An alternative `DeepLTranslator` implementation MUST be selectable via `TRANSLATION_PROVIDER=deepl` without changes to call-path code.

#### Scenario: Default LLM-based translation
- **WHEN** `TRANSLATION_PROVIDER` is unset or `llm`
- **THEN** translation is performed via a Groq LLM prompt and no additional API keys are required beyond `GROQ_API_KEY`

#### Scenario: DeepL provider selected
- **WHEN** `TRANSLATION_PROVIDER=deepl` and `DEEPL_API_KEY` is set
- **THEN** translation is performed via the DeepL API
