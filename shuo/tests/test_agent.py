"""
Test scaffold for Phase 6 agent framework migration (AGENT-01 through AGENT-05).

Task IDs:
  6-01-01 — AGENT-01/AGENT-02: streaming tokens, DTMF tool, hangup tool
  6-01-02 — AGENT-02/AGENT-03: hold_continue no-TTS, marker scanner deletion
  6-02-02 — AGENT-05: LLM_MODEL env var with provider prefix

Tests 1-4, 7-8 should pass after Task 1 (LLMService rewrite).
Tests 5-6 (test_marker_scanner_deleted, test_agent_no_marker_fields) are RED
until Plan 02 removes MarkerScanner from agent.py.
"""

import os
import pytest
from unittest.mock import AsyncMock, patch


# =============================================================================
# Task ID 6-01-01 — AGENT-01: LLMService streams text tokens
# =============================================================================

@pytest.mark.asyncio
async def test_llm_service_streams_text_tokens():
    """LLMService.start() calls on_token at least once and on_done exactly once."""
    from shuo.services.llm import LLMService
    from pydantic_ai.models.test import TestModel

    tokens = []
    done_count = [0]

    async def on_token(t: str) -> None:
        tokens.append(t)

    async def on_done() -> None:
        done_count[0] += 1

    llm = LLMService(on_token=on_token, on_done=on_done)

    # Use TestModel for deterministic results — it returns a short canned response
    with llm._agent.override(model=TestModel()):
        await llm.start("Hello")
        # Wait for the background task to complete
        if llm._task:
            await llm._task

    assert len(tokens) > 0, "on_token was never called — no tokens streamed"
    assert done_count[0] == 1, f"on_done called {done_count[0]} times, expected 1"


# =============================================================================
# Task ID 6-01-01 — AGENT-02: press_dtmf tool populates dtmf_queue
# =============================================================================

@pytest.mark.asyncio
async def test_llm_service_press_dtmf_tool():
    """press_dtmf tool call populates turn_context.dtmf_queue."""
    from shuo.services.llm import LLMService, LLMTurnContext
    from pydantic_ai.models.test import TestModel

    tokens = []
    done_count = [0]

    async def on_token(t: str) -> None:
        tokens.append(t)

    async def on_done() -> None:
        done_count[0] += 1

    llm = LLMService(on_token=on_token, on_done=on_done)

    # TestModel auto-calls all registered tools, so press_dtmf will fire
    with llm._agent.override(model=TestModel(call_tools=["press_dtmf"])):
        await llm.start("Press 1 for sales")
        if llm._task:
            await llm._task

    assert done_count[0] == 1, "on_done was not called"
    # After TestModel fires press_dtmf, dtmf_queue should have an entry
    assert len(llm.turn_context.dtmf_queue) > 0, (
        "press_dtmf was not called — dtmf_queue is empty"
    )


# =============================================================================
# Task ID 6-01-01 — AGENT-02: signal_hangup tool sets hangup_pending
# =============================================================================

@pytest.mark.asyncio
async def test_llm_service_signal_hangup_tool():
    """signal_hangup tool call sets turn_context.hangup_pending = True."""
    from shuo.services.llm import LLMService
    from pydantic_ai.models.test import TestModel

    tokens = []
    done_count = [0]

    async def on_token(t: str) -> None:
        tokens.append(t)

    async def on_done() -> None:
        done_count[0] += 1

    llm = LLMService(on_token=on_token, on_done=on_done)

    # TestModel will call signal_hangup tool
    with llm._agent.override(model=TestModel(call_tools=["signal_hangup"])):
        await llm.start("End the call")
        if llm._task:
            await llm._task

    assert done_count[0] == 1, "on_done was not called"
    assert llm.turn_context.hangup_pending is True, (
        "signal_hangup was not called — hangup_pending is False"
    )


# =============================================================================
# Task ID 6-01-02 — AGENT-02: signal_hold_continue suppresses TTS
# =============================================================================

@pytest.mark.asyncio
async def test_llm_service_hold_continue_no_tts():
    """When signal_hold_continue fires with no text, on_token is NOT called."""
    from shuo.services.llm import LLMService
    from pydantic_ai.models.test import TestModel

    tokens = []
    done_count = [0]

    async def on_token(t: str) -> None:
        tokens.append(t)

    async def on_done() -> None:
        done_count[0] += 1

    llm = LLMService(on_token=on_token, on_done=on_done)

    # TestModel with only signal_hold_continue tool — no text output
    with llm._agent.override(model=TestModel(call_tools=["signal_hold_continue"])):
        await llm.start("[HOLD_CHECK] Hold music playing")
        if llm._task:
            await llm._task

    assert done_count[0] == 1, "on_done was not called"
    assert llm.turn_context.hold_continue is True, (
        "signal_hold_continue was not called — hold_continue is False"
    )
    # When only hold_continue fires with no text, on_token should not be called
    # (TestModel with call_tools=["signal_hold_continue"] emits no text)
    assert len(tokens) == 0, (
        f"on_token was called {len(tokens)} times — expected 0 for hold_continue turn"
    )


# =============================================================================
# Task ID 6-01-02 — AGENT-03: MarkerScanner deleted (Plan 02 RED test)
# =============================================================================

@pytest.mark.asyncio
async def test_marker_scanner_deleted():
    """MarkerScanner class should not exist in shuo.agent after Plan 02."""
    import shuo.agent
    assert not hasattr(shuo.agent, "MarkerScanner"), (
        "MarkerScanner still present in shuo.agent — Plan 02 has not removed it yet"
    )


# =============================================================================
# Task ID 6-01-02 — AGENT-03: Agent has no _scanner field (Plan 02 RED test)
# =============================================================================

@pytest.mark.asyncio
async def test_agent_no_marker_fields():
    """Agent instance should not have _scanner attribute after Plan 02."""
    from shuo.agent import Agent

    agent = Agent.__new__(Agent)
    assert not hasattr(agent, "_scanner"), (
        "Agent._scanner still present — Plan 02 has not removed MarkerScanner wiring yet"
    )


# =============================================================================
# Task ID 6-02-02 — AGENT-05: LLM_MODEL=groq:... selects groq provider
# =============================================================================

@pytest.mark.asyncio
async def test_llm_model_groq_prefix():
    """LLM_MODEL=groq:llama-3.3-70b-versatile → agent model is groq:llama-3.3-70b-versatile."""
    import importlib

    model_string = "groq:llama-3.3-70b-versatile"

    with patch.dict(os.environ, {"LLM_MODEL": model_string}):
        # Reload the module so the module-level _agent is reconstructed with new env var
        import shuo.services.llm as llm_module
        importlib.reload(llm_module)
        LLMService = llm_module.LLMService  # noqa: N806

        async def noop_token(t: str) -> None:
            pass

        async def noop_done() -> None:
            pass

        llm = LLMService(on_token=noop_token, on_done=noop_done)
        # The internal _agent model should reflect the env var
        agent_model = llm._agent.model
        # pydantic-ai stores the model string or a model object; check string representation
        model_repr = str(agent_model)
        assert model_string in model_repr or "groq" in model_repr.lower(), (
            f"Expected model string to contain '{model_string}', got: {model_repr!r}"
        )


# =============================================================================
# Task ID 6-02-02 — AGENT-05: LLM_MODEL=openai:... selects openai provider
# =============================================================================

@pytest.mark.asyncio
async def test_llm_model_openai_prefix():
    """LLM_MODEL=openai:gpt-4o → agent model is openai:gpt-4o."""
    import importlib

    model_string = "openai:gpt-4o"

    with patch.dict(os.environ, {"LLM_MODEL": model_string}):
        import shuo.services.llm as llm_module
        importlib.reload(llm_module)
        LLMService = llm_module.LLMService  # noqa: N806

        async def noop_token(t: str) -> None:
            pass

        async def noop_done() -> None:
            pass

        llm = LLMService(on_token=noop_token, on_done=noop_done)
        agent_model = llm._agent.model
        model_repr = str(agent_model)
        assert model_string in model_repr or "openai" in model_repr.lower(), (
            f"Expected model string to contain '{model_string}', got: {model_repr!r}"
        )
