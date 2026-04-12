## Why

Voice agents are currently limited to single-language conversations, making them inaccessible in multilingual contexts. This feature enables agents to bridge language gaps — translating caller speech into the agent's operating language, and translating agent responses back before delivery to the caller.

## What Changes

- Add a configurable translation stage in the call pipeline: after STT output and before LLM input, translate transcript from caller's language to the agent's target language
- Add a second translation stage before TTS: translate LLM output from the agent's language back to the caller's language
- Expose `source_language` and `target_language` configuration options (per-call and globally)
- Language detection can be automatic (default) or explicitly configured

## Capabilities

### New Capabilities

- `call-translation`: Bidirectional translation layer that sits between STT→LLM and LLM→TTS, translating caller speech into the agent's language and agent responses back to the caller's language

### Modified Capabilities

<!-- none -->

## Impact

- **`shuo/call.py`**: Translation action type added; `step()` may emit translate actions alongside existing actions
- **`shuo/agent.py`**: Translation step inserted before LLM input (STT→translate→LLM) and before TTS (LLM→translate→TTS)
- **`shuo/language.py`**: Optionally, translation can be done via a separate API call (e.g., DeepL, Google Translate, or an LLM prompt)
- **New module `shuo/translation.py`**: Translation provider abstraction (LLM-based or dedicated API)
- **Config/CLI**: New env vars `TRANSLATION_SOURCE_LANG` and `TRANSLATION_TARGET_LANG`; `voice-agent serve` gains optional `--source-lang` / `--target-lang` flags
- **No breaking changes** to existing single-language deployments (translation is a no-op when not configured)
