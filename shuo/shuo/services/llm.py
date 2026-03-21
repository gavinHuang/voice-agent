"""
LLM service using pydantic-ai Agent with typed tool calls and iter()-based streaming.
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

CRITICAL RULE for ending calls — two steps over TWO separate responses:
Step 1: When your goal is accomplished, summarise or confirm the details and ask "does that work for you?" or similar. STOP and wait for their reply. Do NOT say goodbye.
Step 2: Only in your NEXT response, after confirmation, say a single short closing sentence (e.g. "Great, thank you. Goodbye!") and call signal_hangup().
NEVER combine step 1 and step 2 in the same response.

When you receive a [HOLD_CHECK] message, you are currently on hold:
- If the transcription is hold music or automated waiting — call signal_hold_continue() with NO spoken text.
- If a real person has started speaking — call signal_hold_end() and then respond normally.

Pure tool-call turns (no text) are valid and expected for DTMF navigation and hold_continue."""


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
    """

    def __init__(
        self,
        on_token: Callable[[str], Awaitable[None]],
        on_done: Callable[[], Awaitable[None]],
        goal: str = "",
    ):
        self._on_token = on_token
        self._on_done = on_done

        self._goal_suffix = (
            f"\n\nYour goal for this call: {goal}\n"
            "Pursue this goal naturally. Do NOT announce your goal — just work towards it. "
            "Once accomplished, confirm details and STOP — wait for their reply. "
            "Only after they confirm, say goodbye and call signal_hangup() in a separate response.\n"
            "IVR NAVIGATION RULE: When you hear a recorded menu listing options, "
            "call press_dtmf() with ONLY the digit — no words, no explanation."
        ) if goal else ""

        # Build the pydantic-ai Agent for this service instance.
        # Created per-instance (not module-level) so API key validation
        # only occurs when a real model is used (tests override via model=).
        _model_string = os.getenv("LLM_MODEL", "groq:llama-3.3-70b-versatile")
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
            return SYSTEM_PROMPT + ctx.deps.goal_suffix

        # Register the five tools
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
        assistant_text = ""
        self._turn_ctx = LLMTurnContext(goal_suffix=self._goal_suffix)

        try:
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
                                    break
                                if (
                                    isinstance(event, PartDeltaEvent)
                                    and isinstance(event.delta, TextPartDelta)
                                    and event.delta.content_delta
                                ):
                                    token = event.delta.content_delta
                                    assistant_text += token
                                    await self._on_token(token)
                    elif Agent.is_call_tools_node(node):
                        async with node.stream(run.ctx) as stream:
                            async for _ in stream:
                                pass  # tools execute; ctx.deps gets mutated

            if self._running:
                self._history = list(run.result.all_messages())
                await self._on_done()

        except asyncio.CancelledError:
            raise

        except Exception as e:
            log.error("Generation failed", e)
            await self._on_done()

        finally:
            self._running = False
            self._task = None
