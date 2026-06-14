"""
language.py — LLM service and system prompts.

LanguageModel streams text tokens and tool side-effects from an LLM call.
resolve_outcome() interprets the completed turn into a TurnOutcome.

Conversation policy (what the agent says, how it uses tools) is in this file
alongside the service mechanics — edit one place, see the whole picture.
"""

import os
import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable, List

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, PartDeltaEvent, TextPartDelta
from pydantic_ai.settings import ModelSettings

from .log import ServiceLogger
from .call import TurnOutcome
from .context import CallContext, build_system_prompt
from .telemetry import CallTelemetry, CP
from .prompts import (
    PROMPT_WITH_TOOLS,
    PROMPT_TEXT_TAGS,
    supports_tools,
    goal_suffix,
    SUPPRESS_RE,
    is_farewell,
)

log = ServiceLogger("LLM")

_LLM_TIMEOUT    = float(os.getenv("LLM_TIMEOUT",     "30.0"))
_LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES",  "1"))


# =============================================================================
# TURN OUTPUT PARSING
#
# Two scenarios handled by the same patterns:
#   1. Tool-capable models (Llama 3.3) that sometimes leak raw function-call
#      syntax as text — we filter it from TTS and parse intents as fallback.
#   2. No-tool models (compound-beta) using the deliberate [TAG] text protocol.
# =============================================================================

_DTMF_TOOL_RE = re.compile(r'press_dtmf\s*\(\s*["\']?([0-9*#])["\']?\s*\)', re.IGNORECASE)
_DTMF_TAG_RE  = re.compile(r'\[DTMF:([0-9*#])\]',                           re.IGNORECASE)


def _dtmf_from_text(text: str) -> list:
    return _DTMF_TOOL_RE.findall(text) or _DTMF_TAG_RE.findall(text)


# =============================================================================
# TURN CONTEXT  (mutable per-turn tool side-effects, private to LanguageModel)
# =============================================================================

@dataclass
class _TurnCtx:
    dtmf_queue:     List[str] = field(default_factory=list)
    hold_start:     bool = False
    hold_end:       bool = False
    hold_continue:  bool = False
    hangup_pending: bool = False


# =============================================================================
# LANGUAGE MODEL
# =============================================================================

class LanguageModel:
    """
    Streams tokens from an LLM and interprets tool calls / text tags.

    Persistent across turns — history is maintained between calls to start().
    resolve_outcome() derives a TurnOutcome after each completed turn.
    """

    def __init__(
        self,
        on_token:   Callable[[str], Awaitable[None]],
        on_done:    Callable[[], Awaitable[None]],
        goal:       str = "",
        ctx:        Optional["CallContext"] = None,
        telemetry:  Optional[CallTelemetry] = None,
        callee_lang: str = "English",
    ):
        self._on_token  = on_token
        self._on_done   = on_done
        self._telemetry = telemetry

        model = os.getenv("LLM_MODEL", "groq:llama-3.3-70b-versatile")
        self._tools_enabled = supports_tools(model)

        if ctx is not None:
            context_suffix = "\n\n" + build_system_prompt(ctx, tools=self._tools_enabled)
        else:
            context_suffix = goal_suffix(goal, self._tools_enabled)

        lang_suffix = (
            f"\n\nIMPORTANT: Always respond in {callee_lang}, regardless of the language of incoming messages."
            if callee_lang.lower() != "english"
            else ""
        )
        prompt = (
            (PROMPT_WITH_TOOLS if self._tools_enabled else PROMPT_TEXT_TAGS)
            + context_suffix
            + lang_suffix
        )

        settings = ModelSettings(
            max_tokens=  int(os.getenv("LLM_MAX_TOKENS",   "500")),
            temperature= float(os.getenv("LLM_TEMPERATURE", "0.7")),
        )

        self._agent: Agent[_TurnCtx, str] = Agent(
            model=model,
            deps_type=_TurnCtx,
            model_settings=settings,
        )

        @self._agent.system_prompt
        def _sys(_ctx: RunContext[_TurnCtx]) -> str:
            return prompt

        if self._tools_enabled:
            @self._agent.tool
            async def press_dtmf(ctx: RunContext[_TurnCtx], digit: str) -> str:
                """Press a DTMF digit on the phone keypad. Use for IVR menu navigation. Send ONLY the digit with no text."""
                ctx.deps.dtmf_queue.append(digit)
                return f"Sending digit {digit!r}"

            @self._agent.tool(retries=0)
            async def signal_hold(ctx: RunContext[_TurnCtx]) -> str:
                """Signal that hold music has been detected."""
                ctx.deps.hold_start = True
                return "Hold mode activated"

            @self._agent.tool(retries=0)
            async def signal_hold_continue(ctx: RunContext[_TurnCtx]) -> str:
                """Signal that hold music is still playing. Do NOT produce any text with this tool call."""
                ctx.deps.hold_continue = True
                return "Still on hold"

            @self._agent.tool(retries=0)
            async def signal_hold_end(ctx: RunContext[_TurnCtx]) -> str:
                """Signal that a real person has returned from hold."""
                ctx.deps.hold_end = True
                return "Person returned"

            @self._agent.tool
            async def signal_hangup(ctx: RunContext[_TurnCtx]) -> str:
                """Signal that the call should be hung up after this response completes."""
                ctx.deps.hangup_pending = True
                return "Will hang up after audio"

            log.info("Tool calling enabled")
        else:
            log.info(f"Tools disabled for {model} — using text-tag protocol")

        self._task:           Optional[asyncio.Task] = None
        self._running:        bool                   = False
        self._history:        List[ModelMessage]     = []
        self._pending:        str                    = ""
        self._ctx:            _TurnCtx               = _TurnCtx()
        self._tokens_emitted: bool                   = False

    # ── Public API ──────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self._running and self._task is not None

    @property
    def history(self) -> List[ModelMessage]:
        return self._history.copy()

    @property
    def turn_context(self) -> _TurnCtx:
        """Exposed for tests that inspect raw tool side-effects."""
        return self._ctx

    def set_history(self, messages: List[ModelMessage]) -> None:
        self._history = list(messages)

    def is_suppressed_token(self, token: str) -> bool:
        """True if this token is raw function-call syntax and should not go to TTS."""
        return bool(SUPPRESS_RE.search(token))

    def resolve_outcome(self, turn_text: str, tts_had_text: bool) -> TurnOutcome:
        """
        Interpret this turn's side-effects into a TurnOutcome routing decision.

        Reads tool results from self._ctx, then falls back to text-tag parsing
        if raw function-call syntax was leaked into the token stream.
        """
        ctx = self._ctx

        dtmf_list    = list(ctx.dtmf_queue)
        hold_continue = ctx.hold_continue
        hold_start    = ctx.hold_start
        hold_end      = ctx.hold_end
        hangup        = ctx.hangup_pending

        if SUPPRESS_RE.search(turn_text):
            t = turn_text.lower()
            if not dtmf_list:
                parsed = _dtmf_from_text(turn_text)
                if parsed:
                    log.info(f"Fallback DTMF from text: {parsed}")
                    dtmf_list = parsed
            if not dtmf_list:
                if not hold_continue and ('signal_hold_continue' in t or '[hold_continue]' in t):
                    log.info("Fallback hold_continue")
                    hold_continue = True
                if not hold_start and (
                    'signal_hold(' in t or
                    ('[hold]' in t and '[hold_continue]' not in t and '[hold_end]' not in t)
                ):
                    log.info("Fallback hold_start")
                    hold_start = True
                if not hold_end and ('signal_hold_end' in t or '[hold_end]' in t):
                    log.info("Fallback hold_end")
                    hold_end = True
            if not hangup and ('signal_hangup' in t or '[hangup]' in t):
                log.info("Fallback hangup")
                hangup = True

        dtmf_digits = "".join(dtmf_list) if dtmf_list else None

        if not hangup and dtmf_digits is None and is_farewell(turn_text):
            log.info("Farewell detected without signal_hangup() — auto-hanging up")
            hangup = True

        return TurnOutcome(
            dtmf_digits=    dtmf_digits,
            hold_continue=  hold_continue,
            emit_hold_start= hold_start,
            emit_hold_end=  hold_end,
            hangup=         hangup,
            has_speech=     tts_had_text and not hold_continue and dtmf_digits is None,
        )

    async def start(self, message: str) -> None:
        if self._running:
            await self.cancel()
        self._pending = message
        self._running = True
        self._task = asyncio.create_task(self._generate())
        log.connected()

    async def cancel(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.cancelled()

    # ── Internal ────────────────────────────────────────────────────

    async def _generate(self) -> None:
        attempt = 0
        self._tokens_emitted = False
        while attempt <= _LLM_MAX_RETRIES:
            try:
                await asyncio.wait_for(self._generate_once(first_attempt=(attempt == 0)), timeout=_LLM_TIMEOUT)
                return
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                log.warning(f"LLM timed out after {_LLM_TIMEOUT}s (attempt {attempt + 1})")
                if attempt < _LLM_MAX_RETRIES and not self._tokens_emitted:
                    attempt += 1
                    continue
                log.error("LLM timed out — ending turn without response")
                break
            except Exception as e:
                log.error(f"Generation failed (attempt {attempt + 1})", e)
                if attempt < _LLM_MAX_RETRIES and not self._tokens_emitted:
                    attempt += 1
                    continue
                break

        if self._running:
            await self._on_done()
        self._running = False
        self._task = None

    async def _generate_once(self, first_attempt: bool = True) -> None:
        self._ctx = _TurnCtx()
        _first_token_recorded = False

        if self._telemetry:
            # Checkpoint only on the first attempt of the first turn (singleton).
            # Subsequent turns/retries skip the checkpoint but always count the turn.
            if first_attempt:
                self._telemetry.checkpoint(CP.LLM_START)
            self._telemetry.increment("llm_turns")

        async with self._agent.iter(
            self._pending,
            deps=self._ctx,
            message_history=self._history,
        ) as run:
            async for node in run:
                if Agent.is_model_request_node(node):
                    async with node.stream(run.ctx) as stream:
                        async for event in stream:
                            if not self._running:
                                return
                            if (
                                isinstance(event, PartDeltaEvent)
                                and isinstance(event.delta, TextPartDelta)
                                and event.delta.content_delta
                            ):
                                self._tokens_emitted = True
                                if self._telemetry and not _first_token_recorded:
                                    _first_token_recorded = True
                                    self._telemetry.checkpoint(CP.LLM_FIRST_TOKEN)
                                await self._on_token(event.delta.content_delta)
                elif Agent.is_call_tools_node(node):
                    async with node.stream(run.ctx) as stream:
                        async for _ in stream:
                            pass  # tools mutate self._ctx

        if self._telemetry and first_attempt:
            self._telemetry.checkpoint(CP.LLM_END)

        if self._running:
            self._history = list(run.result.all_messages())
            await self._on_done()
        self._running = False
        self._task = None
