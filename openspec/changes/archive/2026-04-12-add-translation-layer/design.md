## Context

The voice agent pipeline today is: STT → LLM → TTS. All three stages operate in the same language. To support cross-language calls, we need two translation points:

1. **Inbound**: after STT, translate the caller's transcript from their language into the agent's operating language before it reaches the LLM
2. **Outbound**: after the LLM produces tokens, translate them from the agent's language into the caller's language before TTS synthesis

The entry point is `Agent.start_turn(transcript)` in `shuo/agent.py`. Inbound translation happens there before passing `message` to `self._llm.start(message)`. Outbound translation wraps the LLM token stream in `_on_llm_token`.

## Goals / Non-Goals

**Goals:**
- Bidirectional per-call translation with configurable source/target languages
- LLM-based translation as the default provider (reuses Groq, no new API dependency)
- Optional dedicated provider support (e.g., DeepL) via the same abstraction
- No-op behavior when translation is not configured (zero latency impact on existing deployments)
- Per-call language override (passed in at call setup time)

**Non-Goals:**
- Real-time language auto-detection from audio (language must be declared explicitly or via config)
- Translating DTMF or control signals
- Translating the system/goal prompt itself
- Multi-language within a single call

## Decisions

### 1. New `shuo/translation.py` module with a `Translator` abstraction

A single `Translator` class with an async `translate(text, source_lang, target_lang) -> str` interface. Initial implementation uses the same Groq LLM with a terse prompt (`"Translate the following <source> text to <target>. Output only the translation:\n\n<text>"`). An optional `DeepLTranslator` can be added later without touching the call path.

**Why over inline LLM call in agent.py:** Keeps translation testable and swappable. The abstraction cost is one small file.

### 2. Inbound translation: full-transcript, one blocking call

The entire STT transcript is translated before the LLM starts. This adds one serial LLM round-trip (~200–400ms) to TTFT for cross-language calls.

**Why not streaming STT + streaming translate:** STT already returns a complete transcript (Deepgram Flux fires `on_utterance` with a final result). There's no partial input to stream. A single call is simpler and correct.

### 3. Outbound translation: buffer full LLM response, then translate, then TTS

The LLM token stream is accumulated in full, then a single translation call is made, then the translated text is sent to TTS as one chunk.

**Alternatives considered:**
- *Sentence-by-sentence streaming translation*: would reduce latency but requires a reliable sentence splitter across languages and adds complexity. Defer to v2.
- *Token-by-token translation*: not feasible — single tokens lack enough context for accurate translation.

**Trade-off:** Outbound path loses streaming TTS benefit (first audio delayed until full LLM response). For cross-language calls this is acceptable since callers already expect a language switch. Can be revisited with sentence buffering later.

### 4. Configuration via env vars + per-call kwargs

Global defaults: `TRANSLATION_SOURCE_LANG` and `TRANSLATION_TARGET_LANG` environment variables. Per-call override via kwargs on `Agent.__init__` and on `voice-agent call --source-lang / --target-lang`. When both are `None`, the translation module short-circuits immediately (identity function).

### 5. No changes to `call.py` state machine

Translation is an implementation detail of `Agent`, not a state-machine concern. The `step()` function and events remain unchanged.

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| +200–400ms inbound latency per turn | Acceptable trade-off for cross-language; document clearly. Could be parallelised with TTS pool warm-up in future. |
| Outbound: full LLM buffer before TTS starts | First audio latency increases. Mitigate in v2 with sentence chunking. |
| LLM translation quality varies by language pair | Use system prompt tuning; fall back to DeepL for critical deployments. |
| Translator uses same Groq rate limit as LLM | Two Groq calls per turn. Monitor and add backoff if needed. |
| Hold-check special tokens (`[HOLD_CONTINUE]`, `[HOLD_END]`) must not be translated | Strip/preserve control tokens before translation; re-inject after. |

## Migration Plan

1. Deploy with `TRANSLATION_SOURCE_LANG` and `TRANSLATION_TARGET_LANG` unset → existing behavior unchanged
2. Set env vars to enable translation for new deployments
3. No database migrations, no breaking API changes
4. Rollback: unset the env vars

## Open Questions

- Should sentence-by-sentence outbound streaming be in scope for v1 or v2?
- Is a dedicated provider (DeepL/Google) required at launch, or is LLM-based translation sufficient?
