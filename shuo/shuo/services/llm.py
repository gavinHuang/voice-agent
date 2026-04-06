"""
LLM service using pydantic-ai Agent with typed tool calls and iter()-based streaming.

Models that support tool calling (e.g. llama-3.3-70b-versatile) use pydantic-ai tools.
Models that don't (e.g. compound-beta) fall back to a text-tag protocol where the LLM
emits [DTMF:1], [HOLD], [HOLD_CONTINUE], [HOLD_END], [HANGUP] tags in its text output.

resolve_outcome() interprets both paths — structured tool side-effects and text-tag
fallbacks — and returns a TurnOutcome value object for the Agent to dispatch.
"""

import os
import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable, List

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, PartDeltaEvent, TextPartDelta
from pydantic_ai.settings import ModelSettings

from ..log import ServiceLogger
from ..prompts import build_system_prompt
from ..types import TurnOutcome

log = ServiceLogger("LLM")

_LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "30.0"))
_LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "1"))


# =============================================================================
# TEXT-TAG PARSING
#
# Two cases handled by the same regexes:
#   1. Tool-capable models (e.g. Llama 3.3) that sometimes leak raw function-call
#      syntax as text instead of structured tool calls.
#   2. No-tool models (e.g. compound-beta) that use the deliberate text-tag protocol:
#      [DTMF:1], [HOLD], [HOLD_CONTINUE], [HOLD_END], [HANGUP]
# =============================================================================

_FC_SUPPRESS_RE = re.compile(
    r'press_dtmf|signal_hold|signal_hangup|function_calls|<function|function>|invoke>'
    r'|\[DTMF:[0-9*#]\]|\[HOLD(?:_CONTINUE|_END)?\]|\[HANGUP\]',
    re.IGNORECASE,
)
_DTMF_TOOL_RE = re.compile(
    r'press_dtmf\s*\(\s*["\']?([0-9*#])["\']?\s*\)',
    re.IGNORECASE,
)
_DTMF_TAG_RE = re.compile(r'\[DTMF:([0-9*#])\]', re.IGNORECASE)


def _dtmf_findall(text: str) -> list:
    return _DTMF_TOOL_RE.findall(text) or _DTMF_TAG_RE.findall(text)


_FAREWELL_PHRASES = (
    "goodbye", "good bye", "bye bye", "bye-bye", "farewell",
    "have a good", "have a great", "have a nice", "take care",
    "talk soon", "speak soon", "all the best", "best of luck",
)


def _looks_like_farewell(text: str) -> bool:
    t = text.lower()
    return any(phrase in t for phrase in _FAREWELL_PHRASES)


# =============================================================================
# TURN CONTEXT (per-turn tool side effects)
# =============================================================================

@dataclass
class LLMTurnContext:
    """Mutable context passed as deps= to pydantic-ai agent per turn.

    Tools mutate this object directly. LLMService reads it after the run
    to determine what events to fire.
    """
    dtmf_queue: List[str] = field(default_factory=list)
    hold_start: bool = False
    hold_end: bool = False
    hold_continue: bool = False
    hangup_pending: bool = False


# =============================================================================
# LLM SERVICE
# =============================================================================

class LLMService:
    """
    pydantic-ai Agent-based LLM service with typed tool calls and iter() streaming.

    Manages conversation history and streams tokens via callback.
    Tools (press_dtmf, signal_hold, signal_hold_continue, signal_hold_end,
    signal_hangup) are registered on the agent and mutate LLMTurnContext.

    For models that don't support tool calling (e.g. compound-beta), tools are
    omitted and the model uses text tags ([DTMF:X], [HANGUP], etc.) instead.
    resolve_outcome() handles both paths via regex fallback.
    """

    def __init__(
        self,
        on_token: Callable[[str], Awaitable[None]],
        on_done: Callable[[], Awaitable[None]],
        goal: str = "",
    ):
        self._on_token = on_token
        self._on_done = on_done

        _model_string = os.getenv("LLM_MODEL", "groq:llama-3.3-70b-versatile")

        # Build full system prompt and discover tool support in one call.
        # Goal is a per-instance constant — baked here, not injected per turn.
        _full_system_prompt, self._tools_enabled = build_system_prompt(goal, _model_string)

        _model_settings = ModelSettings(
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "500")),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
        )

        self._agent: Agent[LLMTurnContext, str] = Agent(
            model=_model_string,
            deps_type=LLMTurnContext,
            model_settings=_model_settings,
        )

        @self._agent.system_prompt
        def _system_prompt(ctx: RunContext[LLMTurnContext]) -> str:
            return _full_system_prompt

        if self._tools_enabled:
            @self._agent.tool
            async def press_dtmf(ctx: RunContext[LLMTurnContext], digit: str) -> str:
                """Press a DTMF digit on the phone keypad. Use for IVR menu navigation. Send ONLY the digit with no text."""
                ctx.deps.dtmf_queue.append(digit)
                return f"DTMF digit {digit!r} will be sent"

            @self._agent.tool
            async def signal_hold(ctx: RunContext[LLMTurnContext]) -> str:
                """Signal that hold music has been detected."""
                ctx.deps.hold_start = True
                return "Hold mode activated"

            @self._agent.tool
            async def signal_hold_continue(ctx: RunContext[LLMTurnContext]) -> str:
                """Signal that hold music is still playing. Do NOT produce any text with this tool call."""
                ctx.deps.hold_continue = True
                return "Continuing to wait on hold"

            @self._agent.tool
            async def signal_hold_end(ctx: RunContext[LLMTurnContext]) -> str:
                """Signal that a real person has returned from hold."""
                ctx.deps.hold_end = True
                return "Hold ended, person detected"

            @self._agent.tool
            async def signal_hangup(ctx: RunContext[LLMTurnContext]) -> str:
                """Signal that the call should be hung up after this response completes."""
                ctx.deps.hangup_pending = True
                return "Call will end after this response"

            log.info("Tool calling enabled")
        else:
            log.info(f"Tool calling disabled for {_model_string} — using text-tag protocol")

        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._history: List[ModelMessage] = []
        self._pending_message: str = ""
        self._turn_ctx: LLMTurnContext = LLMTurnContext()

    @property
    def is_active(self) -> bool:
        return self._running and self._task is not None

    @property
    def history(self) -> List[ModelMessage]:
        return self._history.copy()

    @property
    def turn_context(self) -> LLMTurnContext:
        """The LLMTurnContext from the most recently completed run."""
        return self._turn_ctx

    def clear_history(self) -> None:
        self._history = []

    def set_history(self, messages: List[ModelMessage]) -> None:
        """Replace conversation history (used for resuming after take-over)."""
        self._history = list(messages)

    def is_suppressed_token(self, token: str) -> bool:
        """True if this token should NOT be forwarded to TTS.

        Detects raw function-call syntax that Llama 3.3 sometimes leaks as
        plain text — e.g. "<function>press_dtmf...</function>". These are
        accumulated in turn_text for fallback parsing but must not be spoken.
        """
        return bool(_FC_SUPPRESS_RE.search(token))

    def resolve_outcome(self, turn_text: str, tts_had_text: bool) -> TurnOutcome:
        """
        Interpret this turn's side-effects into a TurnOutcome routing decision.

        Reads tool results from the completed turn_context, then applies regex
        fallback parsing for function-call leakage (Llama 3.3) and the
        deliberate text-tag protocol used by no-tool models (compound-beta).

        Priority order:
          1. hold_continue  → silent done (skip TTS)
          2. dtmf_digits    → send digit, suppress speech
          3. has_speech     → flush TTS
          4. (else)         → empty turn, silent done
        """
        ctx = self._turn_ctx

        dtmf_list = list(ctx.dtmf_queue)
        hold_continue = ctx.hold_continue
        hold_start = ctx.hold_start
        hold_end = ctx.hold_end
        hangup = ctx.hangup_pending

        # Fallback: parse text tags when raw function-call syntax was detected.
        if _FC_SUPPRESS_RE.search(turn_text):
            t = turn_text.lower()
            if not dtmf_list:
                text_dtmf = _dtmf_findall(turn_text)
                if text_dtmf:
                    log.info(f"Fallback DTMF parsed from text: {text_dtmf}")
                    dtmf_list = text_dtmf
            if not dtmf_list:
                if not hold_continue and ('signal_hold_continue' in t or '[hold_continue]' in t):
                    log.info("Fallback hold_continue detected in text")
                    hold_continue = True
                if not hold_start and (
                    'signal_hold(' in t or
                    ('[hold]' in t and '[hold_continue]' not in t and '[hold_end]' not in t)
                ):
                    log.info("Fallback hold_start detected in text")
                    hold_start = True
                if not hold_end and ('signal_hold_end' in t or '[hold_end]' in t):
                    log.info("Fallback hold_end detected in text")
                    hold_end = True
            if not hangup and ('signal_hangup' in t or '[hangup]' in t):
                log.info("Fallback hangup detected in text")
                hangup = True

        dtmf_digits = "".join(dtmf_list) if dtmf_list else None

        # Farewell fallback — LLM said goodbye but forgot to call signal_hangup().
        if not hangup and dtmf_digits is None and _looks_like_farewell(turn_text):
            log.info("Farewell detected without signal_hangup() — auto-hanging up after audio")
            hangup = True

        has_speech = tts_had_text and not hold_continue and dtmf_digits is None

        return TurnOutcome(
            dtmf_digits=dtmf_digits,
            hold_continue=hold_continue,
            emit_hold_start=hold_start,
            emit_hold_end=hold_end,
            hangup=hangup,
            has_speech=has_speech,
        )

    async def start(self, user_message: str) -> None:
        """Start generating a response."""
        if self._running:
            await self.cancel()

        self._pending_message = user_message
        self._running = True
        self._task = asyncio.create_task(self._generate())
        log.connected()

    async def cancel(self) -> None:
        """Cancel ongoing generation."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        log.cancelled()

    async def _generate(self) -> None:
        """Generate response using pydantic-ai agent.iter(), streaming tokens via callback."""
        attempt = 0
        while attempt <= _LLM_MAX_RETRIES:
            try:
                await asyncio.wait_for(
                    self._generate_once(),
                    timeout=_LLM_TIMEOUT,
                )
                return
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                log.warning(f"LLM generation timed out after {_LLM_TIMEOUT}s (attempt {attempt + 1})")
                if attempt < _LLM_MAX_RETRIES:
                    attempt += 1
                    log.info(f"Retrying LLM generation (attempt {attempt + 1}/{_LLM_MAX_RETRIES + 1})")
                    continue
                log.error("LLM generation timed out — ending turn without response")
                break
            except Exception as e:
                log.error(f"Generation failed (attempt {attempt + 1})", e)
                if attempt < _LLM_MAX_RETRIES:
                    attempt += 1
                    log.info(f"Retrying LLM generation (attempt {attempt + 1}/{_LLM_MAX_RETRIES + 1})")
                    continue
                break

        if self._running:
            await self._on_done()
        self._running = False
        self._task = None

    async def _generate_once(self) -> None:
        """Single generation attempt — raises on error, caller handles retry."""
        self._turn_ctx = LLMTurnContext()

        async with self._agent.iter(
            self._pending_message,
            deps=self._turn_ctx,
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
                                token = event.delta.content_delta
                                await self._on_token(token)
                elif Agent.is_call_tools_node(node):
                    async with node.stream(run.ctx) as stream:
                        async for _ in stream:
                            pass  # tools execute; ctx.deps gets mutated

        if self._running:
            self._history = list(run.result.all_messages())
            await self._on_done()
        self._running = False
        self._task = None
