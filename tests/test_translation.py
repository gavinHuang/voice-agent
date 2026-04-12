"""
Tests for shuo/translation.py and translation integration in Agent.
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# 5.1 — LLMTranslator: prompt format and returned text
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_translator_calls_groq_with_correct_prompt():
    """LLMTranslator sends a well-formed chat completion to Groq and returns content."""
    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    fake_response.choices[0].message.content = "Hola mundo"

    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_fake"}):
        from shuo.translation import LLMTranslator
        translator = LLMTranslator()

    translator._client.chat = MagicMock()
    translator._client.chat.completions = MagicMock()
    translator._client.chat.completions.create = AsyncMock(return_value=fake_response)

    result = await translator.translate("Hello world", "English", "Spanish")

    assert result == "Hola mundo"
    call_args = translator._client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    system_msg = messages[0]
    user_msg = messages[1]
    assert system_msg["role"] == "system"
    assert "English" in system_msg["content"]
    assert "Spanish" in system_msg["content"]
    assert user_msg["role"] == "user"
    assert user_msg["content"] == "Hello world"


@pytest.mark.asyncio
async def test_llm_translator_returns_empty_string_unchanged():
    """LLMTranslator short-circuits on empty/whitespace input without calling API."""
    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_fake"}):
        from shuo.translation import LLMTranslator
        translator = LLMTranslator()

    translator._client.chat = MagicMock()
    translator._client.chat.completions = MagicMock()
    translator._client.chat.completions.create = AsyncMock()

    result = await translator.translate("   ", "English", "Spanish")

    assert result == "   "
    translator._client.chat.completions.create.assert_not_called()


# ---------------------------------------------------------------------------
# 5.2 — No-op short-circuit: same language or None config
# ---------------------------------------------------------------------------

def test_get_translator_returns_none_when_unconfigured():
    """get_translator() returns None when neither env var is set."""
    env = {k: "" for k in ["CALLER_LANG", "CALLEE_LANG"]}
    with patch.dict(os.environ, env, clear=False):
        # Temporarily clear the vars
        for k in env:
            os.environ.pop(k, None)
        from importlib import reload
        import shuo.translation as mod
        result = mod.get_translator()
    assert result is None


def test_get_translator_returns_none_when_same_language():
    """get_translator() returns None when caller lang == callee lang (no-op)."""
    with patch.dict(os.environ, {
        "CALLER_LANG": "English",
        "CALLEE_LANG": "English",
    }):
        from shuo.translation import get_translator
        result = get_translator()
    assert result is None


def test_extract_speech_text_strips_control_tokens():
    """extract_speech_text removes control tokens and returns clean speech."""
    from shuo.translation import extract_speech_text

    assert extract_speech_text("Hello! [HANGUP]") == "Hello!"
    assert extract_speech_text("[HOLD_CONTINUE]") == ""
    assert extract_speech_text("Press 1 [DTMF:1] for sales") == "Press 1  for sales".strip()
    assert extract_speech_text("Your booking is confirmed.\n[HANGUP]") == "Your booking is confirmed."
    assert extract_speech_text("Pure speech here") == "Pure speech here"


# ---------------------------------------------------------------------------
# 5.3 — Inbound path: translated text reaches mock LLM
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_translates_inbound_transcript():
    """Agent.start_turn() translates the transcript before passing to LLM."""
    from shuo.translation import Translator

    class FakeTranslator(Translator):
        async def translate(self, text, source_lang, target_lang):
            return f"[translated:{target_lang}] {text}"

    received_messages = []

    class FakeLLM:
        async def start(self, message):
            received_messages.append(message)

        async def cancel(self): pass

        @property
        def history(self): return []

    fake_phone = MagicMock()
    fake_voice = AsyncMock()
    fake_voice.send = AsyncMock()
    fake_voice.cancel = AsyncMock()
    fake_voice.flush = AsyncMock()

    fake_pool = AsyncMock()
    fake_pool.get = AsyncMock(return_value=fake_voice)

    fake_tracer = MagicMock()
    fake_tracer.begin_turn = MagicMock(return_value=1)
    fake_tracer.begin = MagicMock()
    fake_tracer.end = MagicMock()
    fake_tracer.mark = MagicMock()
    fake_tracer.cancel_turn = MagicMock()

    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_fake"}):
        from shuo.agent import Agent
        agent = Agent(
            phone=fake_phone,
            stream_sid="sid",
            emit=lambda e: None,
            voice_pool=fake_pool,
            tracer=fake_tracer,
            translator=FakeTranslator(),
            caller_lang="English",
            callee_lang="Spanish",
        )
        agent._llm = FakeLLM()

        await agent.start_turn("Hola mundo")

    assert len(received_messages) == 1
    assert received_messages[0] == "[translated:English] Hola mundo"


# ---------------------------------------------------------------------------
# 5.4 — Outbound path: TTS receives translated text; control tokens preserved
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_translates_outbound_response():
    """LLM output is translated before TTS; control tokens bypass translation."""
    from shuo.translation import Translator

    translated_calls = []

    class FakeTranslator(Translator):
        async def translate(self, text, source_lang, target_lang):
            translated_calls.append((text, source_lang, target_lang))
            return f"TRANSLATED({text})"

    tts_sent = []

    class FakeTTS:
        async def send(self, text):
            tts_sent.append(text)
        async def flush(self): pass
        async def cancel(self): pass

    fake_pool = AsyncMock()
    fake_pool.get = AsyncMock(return_value=FakeTTS())

    fake_tracer = MagicMock()
    fake_tracer.begin_turn = MagicMock(return_value=1)
    fake_tracer.begin = MagicMock()
    fake_tracer.end = MagicMock()
    fake_tracer.mark = MagicMock()

    outcomes = []

    class FakeLLM:
        async def start(self, message): pass
        async def cancel(self): pass

        def resolve_outcome(self, text, tts_had_text):
            from shuo.call import TurnOutcome
            return TurnOutcome(has_speech=tts_had_text)

        def is_suppressed_token(self, token):
            return False

        @property
        def history(self): return []

    emitted = []
    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_fake"}):
        from shuo.agent import Agent
        agent = Agent(
            phone=MagicMock(),
            stream_sid="sid",
            emit=lambda e: emitted.append(e),
            voice_pool=fake_pool,
            tracer=fake_tracer,
            translator=FakeTranslator(),
            caller_lang="English",
            callee_lang="Spanish",
        )
        agent._llm = FakeLLM()

        # Simulate a full turn: acquire TTS, accumulate tokens, fire done
        await agent.start_turn("test input")
        # Reset: only care about the outbound translation from _on_llm_done
        translated_calls.clear()
        agent._active = True
        agent._current_turn_text = "Your reservation has been confirmed."
        agent._tts_had_text = False

        await agent._on_llm_done()

    # Translation was called with the speech text, in reverse direction (target→source)
    assert len(translated_calls) == 1
    text, src, tgt = translated_calls[0]
    assert "confirmed" in text
    assert src == "English"
    assert tgt == "Spanish"
    # TTS received translated text
    assert any("TRANSLATED" in s for s in tts_sent)


@pytest.mark.asyncio
async def test_agent_control_tokens_not_in_tts():
    """Control tokens like [HANGUP] are not sent to TTS even with translation."""
    from shuo.translation import Translator

    tts_sent = []

    class FakeTranslator(Translator):
        async def translate(self, text, source_lang, target_lang):
            return f"TRANSLATED({text})"

    class FakeTTS:
        async def send(self, text):
            tts_sent.append(text)
        async def flush(self): pass
        async def cancel(self): pass

    fake_pool = AsyncMock()
    fake_pool.get = AsyncMock(return_value=FakeTTS())

    fake_tracer = MagicMock()
    fake_tracer.begin_turn = MagicMock(return_value=1)
    fake_tracer.begin = MagicMock()
    fake_tracer.end = MagicMock()
    fake_tracer.mark = MagicMock()

    class FakeLLM:
        async def start(self, message): pass
        async def cancel(self): pass

        def resolve_outcome(self, text, tts_had_text):
            from shuo.call import TurnOutcome
            return TurnOutcome(hangup=True, has_speech=tts_had_text)

        def is_suppressed_token(self, token): return False

        @property
        def history(self): return []

    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_fake"}):
        from shuo.agent import Agent
        agent = Agent(
            phone=MagicMock(),
            stream_sid="sid",
            emit=lambda e: None,
            voice_pool=fake_pool,
            tracer=fake_tracer,
            translator=FakeTranslator(),
            caller_lang="English",
            callee_lang="Spanish",
        )
        agent._llm = FakeLLM()

        await agent.start_turn("test input")
        agent._active = True
        # LLM responded with only a control token — no speech
        agent._current_turn_text = "[HANGUP]"
        agent._tts_had_text = False

        await agent._on_llm_done()

    # TTS should not have received anything (no speech text after stripping control tokens)
    assert tts_sent == []


# ---------------------------------------------------------------------------
# 5.5 — Startup validation: partial config logs warning and disables translation
# ---------------------------------------------------------------------------

def test_get_translator_one_lang_set_uses_english_default():
    """Setting only CALLER_LANG enables translation with English as the default CALLEE_LANG."""
    with patch.dict(os.environ, {
        "CALLER_LANG": "Spanish",
        "GROQ_API_KEY": "gsk_fake",
    }, clear=False):
        os.environ.pop("CALLEE_LANG", None)
        from shuo.translation import get_translator
        result = get_translator()
    # Spanish ↔ English should return a translator (not None)
    assert result is not None


def test_get_translator_comment_value_falls_back_to_english():
    """A value like '# caller's language' (dotenv parsing artifact) is treated as English."""
    with patch.dict(os.environ, {
        "CALLER_LANG": "# caller's language",
        "CALLEE_LANG": "# agent's operating language",
    }):
        from shuo.translation import get_translator
        result = get_translator()
    # Both default to English → same language → no-op
    assert result is None
