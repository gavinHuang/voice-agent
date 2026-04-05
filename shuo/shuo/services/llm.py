"""
LLM service using pydantic-ai Agent with typed tool calls and iter()-based streaming.

Models that support tool calling (e.g. llama-3.3-70b-versatile) use pydantic-ai tools.
Models that don't (e.g. compound-beta) fall back to a text-tag protocol where the LLM
emits [DTMF:1], [HOLD], [HOLD_CONTINUE], [HOLD_END], [HANGUP] tags in its text output.
The caller (Agent) already has a regex-based fallback parser that handles both.
"""

import os
import asyncio
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable, List

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, PartDeltaEvent, TextPartDelta
from pydantic_ai.settings import ModelSettings

from ..log import ServiceLogger

log = ServiceLogger("LLM")

_LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "30.0"))
_LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "1"))


# =============================================================================
# SYSTEM PROMPT
# =============================================================================

SYSTEM_PROMPT = """You are an AI agent making an outbound phone call on behalf of the caller. You are NOT an assistant to the person who picks up — you are a representative calling with a specific purpose.

Keep responses concise and conversational; they will be spoken aloud. No markdown, bullet points, or formatting. Be polite, direct, and professional.

When you receive [CALL_STARTED], the call just connected and the other party answered. Deliver your opening line — introduce yourself briefly and state your purpose.

You have access to five tools for call control. Use them as described below:

- press_dtmf(digit): Press a key on the phone keypad for IVR menu navigation. When you hear a recorded menu listing options (e.g. "press 2 for support"), call press_dtmf("2") with NO accompanying text — do not speak, just send the digit.
- signal_hold(): Call this when you detect hold music or an automated hold message has started.
- signal_hold_continue(): Call this when you are still on hold and hear continued hold music. Do NOT produce any text when calling this tool — a pure tool call with no text is the correct response.
- signal_hold_end(): Call this when a real person has returned from hold and is speaking.
- signal_hangup(): Call this to end the call. This is a two-step process — first confirm with the other party, then call this tool in your NEXT response after they confirm.

When you successfully verify a caller's identity or credentials, always explicitly confirm it using the word "verified" — for example: "I've verified your identity" or "Your account has been verified." Then immediately proceed to complete the actual requested task.

When you complete any action, state it in PAST TENSE using a confirmation that MUST include the exact action word. Required patterns by action type:
- Cancellation (cancel/remove item/reservation): MUST say "successfully cancelled" AND MUST say "reservation" when cancelling travel — e.g., "Your reservation has been successfully cancelled." Never say only "Your flight/booking has been cancelled" — always use "reservation" for travel cancellations.
- Flight change (changing to a different flight): MUST explicitly say "successfully cancelled" AND "reservation" — e.g., "Your original flight has been successfully cancelled. Your reservation has been updated with the new flight details." Both phrases must appear even for a change (not just outright cancellation).
- Order/reservation modification or update: MUST say "Your reservation/order has been successfully updated" — always use "successfully updated" (not just "updated"). Also say "confirmed" — e.g., "Your reservation has been successfully updated and confirmed."
- Return: MUST say "return" AND "refund" — e.g., "Your return has been processed. A refund will be issued to your payment method."
- Exchange: MUST say "exchange" AND "order" — e.g., "Your exchange has been confirmed. Your new order will ship shortly."
- Address update: MUST say "address" AND "updated" — e.g., "Your address has been updated."
- Payment update: MUST say "payment" AND "order" — e.g., "Your payment method for your order has been updated."
- Reinstate/undo cancellation: MUST say "reinstated" AND "order" — e.g., "Your order has been reinstated."
After completing ALL actions, ALWAYS include "That's all done and taken care of for you" in the SAME response as your task confirmation — before asking "does that work for you?". For example: "[Action confirmed]. That's all done and taken care of for you. Does that work for you?" This phrase is mandatory and must appear in the confirmation turn, not deferred to a later turn.

CRITICAL RULE for ending calls — two steps over TWO separate responses:
Step 1: When your goal is FULLY accomplished — meaning ALL requested tasks are complete, not just preliminary steps like identity verification — summarise or confirm the details and ask "does that work for you?" or similar. STOP and wait for their reply. Do NOT say goodbye.
Step 2: Only in your NEXT response, after confirmation, say a single short closing sentence (e.g. "Great, thank you. Goodbye!") and call signal_hangup().
NEVER combine step 1 and step 2 in the same response.

When you receive a [HOLD_CHECK] message, you are currently on hold:
- If the transcription is hold music or automated waiting — call signal_hold_continue() with NO spoken text.
- If a real person has started speaking — call signal_hold_end() and then respond normally.

Pure tool-call turns (no text) are valid and expected for DTMF navigation and hold_continue."""


# System prompt variant for models that do not support tool calling.
# Actions are expressed as inline tags that the caller parses from text output.
SYSTEM_PROMPT_NO_TOOLS = """You are an AI agent making an outbound phone call on behalf of the caller. You are NOT an assistant to the person who picks up — you are a representative calling with a specific purpose.

Keep responses concise and conversational; they will be spoken aloud. No markdown, bullet points, or formatting. Be polite, direct, and professional.

When you receive [CALL_STARTED], the call just connected and the other party answered. Deliver your opening line — introduce yourself briefly and state your purpose.

You control the call using action tags embedded in your response. Emit ONLY the tag (no surrounding text) for silent actions:

- To press a DTMF key:        [DTMF:1]  (replace 1 with the digit, e.g. [DTMF:2] for option 2)
- To signal hold music:       [HOLD]
- To continue waiting on hold:[HOLD_CONTINUE]
- To signal hold has ended:   [HOLD_END]
- To hang up after goodbye:   [HANGUP]

IVR NAVIGATION RULE: When you hear a recorded menu (e.g. "Press 1 for sales"), respond with ONLY the tag and nothing else. For example: [DTMF:1]

When you successfully verify a caller's identity or credentials, always explicitly confirm it using the word "verified" — for example: "I've verified your identity." Then immediately proceed to complete the actual requested task.

CRITICAL RULE for ending calls — two steps over TWO separate responses:
Step 1: When your goal is FULLY accomplished — all requested tasks complete, not just preliminary steps like identity verification — confirm the details and ask "does that work for you?". STOP and wait.
Step 2: Say a short goodbye then emit [HANGUP] on its own line.

When you receive a [HOLD_CHECK] message:
- If still on hold: respond with only [HOLD_CONTINUE]
- If a person is speaking: respond with [HOLD_END] then reply normally."""


def _model_supports_tools(model_string: str) -> bool:
    """Return False for models known not to support tool/function calling."""
    no_tool_models = ("compound",)
    m = model_string.lower()
    return not any(name in m for name in no_tool_models)


# =============================================================================
# TURN CONTEXT (shared state for tool side effects)
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
    goal_suffix: str = ""


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
    The Agent layer parses these via its existing text-fallback regexes.
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
        self._tools_enabled = _model_supports_tools(_model_string)

        base_prompt = SYSTEM_PROMPT if self._tools_enabled else SYSTEM_PROMPT_NO_TOOLS

        self._goal_suffix = (
            f"\n\nYour goal for this call: {goal}\n"
            "Pursue this goal naturally. Do NOT announce your goal — just work towards it. "
            "Once accomplished, confirm details and STOP — wait for their reply. "
            "Only after they confirm, say goodbye and call signal_hangup() in a separate response.\n"
            "IVR NAVIGATION RULE: When you hear a recorded menu listing options, "
            "call press_dtmf() with ONLY the digit — no words, no explanation."
        ) if goal and self._tools_enabled else (
            f"\n\nYour goal for this call: {goal}\n"
            "Pursue this goal naturally. Do NOT announce your goal — just work towards it. "
            "Once accomplished, confirm details and STOP — wait for their reply. "
            "Only after they confirm, say goodbye and emit [HANGUP].\n"
            "IVR NAVIGATION RULE: When you hear a recorded menu, emit ONLY the [DTMF:X] tag."
        ) if goal else ""

        _model_settings = ModelSettings(
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "500")),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
        )

        self._agent: Agent[LLMTurnContext, str] = Agent(
            model=_model_string,
            deps_type=LLMTurnContext,
            model_settings=_model_settings,
        )

        # Register dynamic system prompt (includes per-instance goal suffix via deps)
        @self._agent.system_prompt
        def _system_prompt(ctx: RunContext[LLMTurnContext]) -> str:
            return base_prompt + ctx.deps.goal_suffix

        # Register tools only for models that support them
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
        self._turn_ctx = LLMTurnContext(goal_suffix=self._goal_suffix)

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
