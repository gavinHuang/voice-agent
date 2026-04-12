## 1. Translator Module

- [x] 1.1 Create `shuo/translation.py` with `Translator` base class and async `translate(text, source_lang, target_lang) -> str` interface
- [x] 1.2 Implement `LLMTranslator` using Groq with a terse system prompt (output translation only, no commentary)
- [x] 1.3 Implement `DeepLTranslator` stub (selectable via `TRANSLATION_PROVIDER=deepl` + `DEEPL_API_KEY`)
- [x] 1.4 Add `get_translator()` factory that reads `TRANSLATION_PROVIDER` and returns the appropriate instance
- [x] 1.5 Add no-op short-circuit: when source == target (or either is None), return text unchanged with zero overhead

## 2. Configuration

- [x] 2.1 Read `TRANSLATION_SOURCE_LANG`, `TRANSLATION_TARGET_LANG`, `TRANSLATION_PROVIDER` from environment in `shuo/config.py` (or equivalent settings location)
- [x] 2.2 Add startup validation: if exactly one of source/target is set, log a warning and disable translation
- [x] 2.3 Add `--source-lang` and `--target-lang` CLI flags to `voice-agent serve` and `voice-agent call` commands in `shuo/cli.py`

## 3. Inbound Translation (STT → LLM)

- [x] 3.1 In `Agent.start_turn()`, after building the `message` string and before calling `self._llm.start(message)`, insert an `await translator.translate(message, source_lang, target_lang)` call
- [x] 3.2 Pass `source_lang` and `target_lang` into `Agent.__init__` (with defaults from config)
- [x] 3.3 Preserve hold-check prefix (`[HOLD_CHECK] ...`) — translate only the `Transcription: <text>` portion, not the entire message

## 4. Outbound Translation (LLM → TTS)

- [x] 4.1 In `Agent._on_llm_done()`, accumulate the full `_current_turn_text` before sending to TTS
- [x] 4.2 Extract and preserve control tokens (`[HOLD_CONTINUE]`, `[HOLD_END]`, `[HANGUP]`) before translation; re-inject after
- [x] 4.3 Translate the text portion with `await translator.translate(text, target_lang, source_lang)` (reverse direction)
- [x] 4.4 Send the translated text to `self._tts.send(translated_text)` as a single chunk instead of streaming tokens

## 5. Tests

- [x] 5.1 Unit test `LLMTranslator`: mock Groq response, verify prompt format and returned text
- [x] 5.2 Unit test no-op short-circuit: same language or None config returns input unchanged
- [x] 5.3 Unit test inbound path in `Agent`: verify translated text reaches the mock LLM, not raw transcript
- [x] 5.4 Unit test outbound path in `Agent`: verify TTS receives translated text and control tokens are preserved
- [x] 5.5 Unit test startup validation: partial config (one lang set) logs warning and disables translation

## 6. Documentation

- [x] 6.1 Add `TRANSLATION_SOURCE_LANG`, `TRANSLATION_TARGET_LANG`, `TRANSLATION_PROVIDER`, `DEEPL_API_KEY` to `.env.example` with comments
- [x] 6.2 Update `CLAUDE.md` environment setup section with the new optional variables
