"""
Agent -- self-contained LLM -> TTS -> Player pipeline.

Encapsulates the entire agent response lifecycle.
Owns conversation history across turns.

    start_turn(transcript)          -> add to history -> LLM -> TTS -> Player -> Twilio
    start_turn(transcript, hold_check=True) -> LLM hold-check -> emit Hold events
    cancel_turn()                   -> cancel all, keep history

TTS connections are managed by TTSPool (see services/tts_pool.py).

Tool effects (DTMF, hold, hangup) are delivered via pydantic-ai tool calls
in LLMService. After each LLM turn, _resolve_turn_outcome() (pure function)
reads LLMService.turn_context and returns a TurnOutcome value object.
_dispatch_outcome() then drives TTS/Player and emits events accordingly.
"""

import asyncio
import re
import time
from typing import Optional, Callable, List, Any

from .services.llm import LLMService
from .services.tts import create_tts
from .services.tts_pool import TTSPool
from .services.player import AudioPlayer
from .services.dtmf import generate_dtmf_ulaw_b64
from .tracer import Tracer
from .log import ServiceLogger
from .types import (
    TurnOutcome,
    AgentTurnDoneEvent, HoldStartEvent, HoldEndEvent,
    HangupPendingEvent, HangupRequestEvent, DTMFToneEvent,
)
from .services.llm import LLMTurnContext

log = ServiceLogger("Agent")

# Regex to detect action tags in the LLM text stream.
# Used for two cases:
#   1. Tool-capable models (e.g. Llama 3.3) that sometimes output raw function-call
#      syntax as text instead of structured tool calls — we suppress and parse them.
#   2. No-tool models (e.g. compound-beta) that use the deliberate text-tag protocol:
#      [DTMF:1], [HOLD], [HOLD_CONTINUE], [HOLD_END], [HANGUP]
_FC_SUPPRESS_RE = re.compile(
    r'press_dtmf|signal_hold|signal_hangup|function_calls|<function|function>|invoke>'
    r'|\[DTMF:[0-9*#]\]|\[HOLD(?:_CONTINUE|_END)?\]|\[HANGUP\]',
    re.IGNORECASE,
)
# Match press_dtmf("1") / press_dtmf(1) style (tool-call leakage)
_DTMF_TOOL_RE = re.compile(
    r'press_dtmf\s*\(\s*["\']?([0-9*#])["\']?\s*\)',
    re.IGNORECASE,
)
# Match [DTMF:1] style (no-tool text-tag protocol)
_DTMF_TAG_RE = re.compile(r'\[DTMF:([0-9*#])\]', re.IGNORECASE)
# Combined: try both patterns
def _DTMF_TEXT_RE_findall(text: str) -> list:
    return _DTMF_TOOL_RE.findall(text) or _DTMF_TAG_RE.findall(text)

# Farewell phrases that indicate the agent is ending the call.
# Used as a fallback when the LLM says goodbye but forgets to call signal_hangup().
_FAREWELL_PHRASES = (
    "goodbye", "good bye", "bye bye", "bye-bye", "farewell",
    "have a good", "have a great", "have a nice", "take care",
    "talk soon", "speak soon", "all the best", "best of luck",
)


def _looks_like_farewell(text: str) -> bool:
    t = text.lower()
    return any(phrase in t for phrase in _FAREWELL_PHRASES)


def _ms_since(t0: float) -> int:
    """Milliseconds elapsed since t0."""
    return int((time.monotonic() - t0) * 1000)


# =============================================================================
# PURE TURN RESOLUTION
# =============================================================================

def _resolve_turn_outcome(
    ctx: LLMTurnContext,
    turn_text: str,
    tts_had_text: bool,
) -> TurnOutcome:
    """
    Pure function: derive TurnOutcome from LLM context + accumulated turn text.

    Reads tool side-effects from ctx (read-only) and applies fallback text
    parsing for function-call leakage and no-tool text-tag protocol models.
    Returns a TurnOutcome value object; all routing decisions are made here,
    making this logic unit-testable without I/O or Agent state.

    Priority order:
      1. hold_continue  → silent done (skip TTS)
      2. dtmf_digits    → send digit, suppress speech
      3. has_speech     → flush TTS
      4. (else)         → empty turn, silent done
    """
    # Start from tool-call results
    dtmf_list = list(ctx.dtmf_queue)
    hold_continue = ctx.hold_continue
    hold_start = ctx.hold_start
    hold_end = ctx.hold_end
    hangup = ctx.hangup_pending

    # Fallback: parse text tags when raw function-call syntax was detected.
    # Handles both tool-call leakage (Llama 3.3) and the deliberate text-tag
    # protocol used by no-tool models like compound-beta.
    if _FC_SUPPRESS_RE.search(turn_text):
        t = turn_text.lower()
        if not dtmf_list:
            text_dtmf = _DTMF_TEXT_RE_findall(turn_text)
            if text_dtmf:
                log.info(f"Fallback DTMF parsed from text: {text_dtmf}")
                dtmf_list = text_dtmf
        # Hold/hangup fallbacks only when no DTMF — they are mutually exclusive
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

    # Farewell fallback — only when no DTMF pending; LLM said goodbye but
    # forgot to call signal_hangup().
    if not hangup and dtmf_digits is None and _looks_like_farewell(turn_text):
        log.info("Farewell detected without signal_hangup() — auto-hanging up after audio")
        hangup = True

    # has_speech: text was generated AND not suppressed by hold_continue or DTMF
    has_speech = tts_had_text and not hold_continue and dtmf_digits is None

    return TurnOutcome(
        dtmf_digits=dtmf_digits,
        hold_continue=hold_continue,
        emit_hold_start=hold_start,
        emit_hold_end=hold_end,
        hangup=hangup,
        has_speech=has_speech,
    )


# =============================================================================
# AGENT
# =============================================================================

class Agent:
    """
    Self-contained agent response pipeline.

    LLM is persistent (keeps conversation history across turns).
    TTS connections come from TTSPool (pre-connected, with TTL eviction).
    Player is created fresh per turn.
    """

    def __init__(
        self,
        isp,
        stream_sid: str,
        emit: Callable[[Any], None],
        tts_pool: TTSPool,
        tracer: Tracer,
        goal: str = "",
        on_token_observed: Optional[Callable[[str], None]] = None,
    ):
        self._isp = isp
        self._stream_sid = stream_sid
        self._emit = emit
        self._tts_pool = tts_pool
        self._tracer = tracer
        self._on_token_observed = on_token_observed

        # Persistent LLM -- keeps conversation history across turns
        self._llm = LLMService(
            on_token=self._on_llm_token,
            on_done=self._on_llm_done,
            goal=goal,
        )

        # Active per-turn services (set during start, cleared on cancel)
        self._tts: Optional[object] = None
        self._player: Optional[AudioPlayer] = None
        self._active = False

        # Current turn number (for tracer)
        self._turn: int = 0

        # Latency milestones (monotonic timestamps, reset each turn)
        self._t0: float = 0.0
        self._t_tts_conn: float = 0.0
        self._t_first_token: float = 0.0
        self._t_first_audio: float = 0.0
        self._got_first_token = False
        self._got_first_audio = False

        # Per-turn accumulators (reset at turn start)
        self._tts_had_text: bool = False
        self._pending_hangup: bool = False
        self._current_turn_text: str = ""
        # _dtmf_queue kept for backward compatibility (populated in legacy path,
        # but routing now uses TurnOutcome.dtmf_digits from _resolve_turn_outcome)
        self._dtmf_queue: List[str] = []

    @property
    def is_turn_active(self) -> bool:
        return self._active

    @property
    def history(self) -> list:
        """Read-only access to conversation history (owned by LLM)."""
        return self._llm.history

    def restore_history(
        self,
        saved_history: list,
        takeover_transcript: List[str],
    ) -> Optional[str]:
        """
        Restore conversation history after take-over hand-back.

        Returns a handback prompt to pass to start_turn() so the agent
        proactively continues the conversation, or None if no takeover
        transcript is available (agent waits for callee to speak).
        """
        self._llm.set_history(saved_history)
        if takeover_transcript:
            return (
                "[HANDBACK] A human supervisor temporarily took over the call. "
                "Here is what was discussed:\n\n"
                + "\n".join(takeover_transcript)
                + "\n\nYou are now back in control. React to what was just "
                "discussed and continue working toward the goal. Be concise."
            )
        return None

    # ── Turn Lifecycle ──────────────────────────────────────────────

    async def start_turn(self, transcript: str, hold_check: bool = False) -> None:
        """Start a new agent turn."""
        if self._active:
            await self.cancel_turn()

        self._active = True
        self._t0 = time.monotonic()
        self._got_first_token = False
        self._got_first_audio = False

        # Reset per-turn accumulators
        self._dtmf_queue = []
        self._tts_had_text = False
        self._pending_hangup = False
        self._current_turn_text = ""

        # Begin tracing this turn
        self._turn = self._tracer.begin_turn(transcript)
        self._tracer.begin(self._turn, "tts_pool")

        # Get TTS from pool (instant if warm, blocks if cold)
        self._tts = await self._tts_pool.get(
            on_audio=self._on_tts_audio,
            on_done=self._on_tts_done,
        )
        self._t_tts_conn = time.monotonic()
        self._tracer.end(self._turn, "tts_pool")

        # Create player
        self._player = AudioPlayer(
            isp=self._isp,
            on_done=self._on_playback_done,
        )

        # Build LLM message — inject hold context when checking hold status
        if hold_check:
            message = (
                "[HOLD_CHECK] You are on hold. Transcription follows. "
                "If automated hold message \u2192 reply [HOLD_CONTINUE] only. "
                "If a real person is speaking \u2192 reply [HOLD_END] then respond normally.\n\n"
                f"Transcription: {transcript}"
            )
        else:
            message = transcript

        # Start LLM
        self._tracer.begin(self._turn, "llm")
        await self._llm.start(message)

        tts_ms = int((self._t_tts_conn - self._t0) * 1000)
        log.info(f"Turn started  (TTS {tts_ms}ms = {tts_ms}ms setup)")

    async def cancel_turn(self) -> None:
        """Cancel current turn, preserve history."""
        if not self._active:
            return

        elapsed = _ms_since(self._t0) if self._t0 else 0
        self._active = False

        # Mark turn as cancelled (ends all open spans)
        self._tracer.cancel_turn(self._turn)

        # Cancel in order: LLM -> TTS -> Player
        await self._llm.cancel()

        if self._tts:
            await self._tts.cancel()
            self._tts = None

        if self._player:
            if self._player.is_playing:
                await self._player.stop_and_clear()
            self._player = None

        log.info(f"Turn cancelled at +{elapsed}ms (history preserved)")

    async def inject_dtmf(self, digit: str) -> None:
        """
        Inject a DTMF tone into the outbound audio stream (dashboard control).

        Generates the tone as u-law audio and sends it directly to Twilio,
        where it is played to the remote party.
        """
        audio = generate_dtmf_ulaw_b64(digit)
        await self._isp.send_audio(audio)

    async def cleanup(self) -> None:
        """Final cleanup when call ends."""
        if self._active:
            await self.cancel_turn()

    # ── Internal Callbacks ──────────────────────────────────────────

    async def _on_llm_token(self, token: str) -> None:
        """LLM produced a text token -> forward to TTS (unless raw function-call syntax)."""
        if not self._active or not self._tts:
            return

        if token:
            self._current_turn_text += token

        # Suppress raw function-call syntax from TTS — Llama 3.3 sometimes outputs
        # tool calls as literal text instead of structured calls. We detect them here
        # so the caller never hears garbled "<function>press_dtmf...</function>" audio.
        if token and _FC_SUPPRESS_RE.search(token):
            log.debug(f"Suppressed function-call token from TTS: {token!r}")
            return

        if not self._got_first_token:
            self._got_first_token = True
            self._t_first_token = time.monotonic()
            self._tracer.mark(self._turn, "llm_first_token")
            self._tracer.begin(self._turn, "tts")
            log.info(f"LLM first token  +{_ms_since(self._t0)}ms")

        if token:
            self._tts_had_text = True
            await self._tts.send(token)
            # Schedule observer on next event-loop turn — never block the LLM token stream (BUG-03)
            if self._on_token_observed:
                asyncio.get_event_loop().call_soon(self._on_token_observed, token)

    async def _on_llm_done(self) -> None:
        """LLM finished -> resolve turn outcome and dispatch effects."""
        if not self._active or not self._tts:
            return

        self._tracer.end(self._turn, "llm")

        outcome = _resolve_turn_outcome(
            self._llm.turn_context,
            self._current_turn_text,
            self._tts_had_text,
        )
        await self._dispatch_outcome(outcome)

    async def _dispatch_outcome(self, outcome: TurnOutcome) -> None:
        """Apply the resolved TurnOutcome: route TTS/Player and fire events."""
        # hold_continue: silent on-hold wait — skip TTS entirely, end turn
        if outcome.hold_continue:
            await self._tts.cancel()
            self._tts = None
            self._player = None
            self._active = False
            self._emit(AgentTurnDoneEvent())
            return

        # Hold state transitions
        if outcome.emit_hold_start:
            self._emit(HoldStartEvent())
        if outcome.emit_hold_end:
            self._emit(HoldEndEvent())

        # Hangup: block new turns immediately; actual hangup fires after audio plays
        if outcome.hangup:
            self._pending_hangup = True
            self._emit(HangupPendingEvent())

        # Route: DTMF > speech > empty
        if outcome.dtmf_digits:
            # DTMF takes priority over text — LLM may generate verbal confirmation
            # alongside the tool call, but IVR navigation must be silent.
            if self._tts_had_text:
                log.debug("DTMF + text: suppressing spoken text, sending digit only")
            await self._tts.cancel()
            self._tts = None
            self._player = None
            self._active = False
            self._emit(DTMFToneEvent(digits=outcome.dtmf_digits))
        elif outcome.has_speech:
            # Normal path: flush TTS; playback completion triggers AgentTurnDoneEvent
            await self._tts.flush()
        else:
            # Empty turn (no text, no DTMF, no hold_continue) — end silently
            await self._tts.cancel()
            self._tts = None
            self._player = None
            self._active = False
            self._emit(AgentTurnDoneEvent())

    async def _on_tts_audio(self, audio_base64: str) -> None:
        """TTS produced audio -> send to player."""
        if not self._active or not self._player:
            return

        if not self._got_first_audio:
            self._got_first_audio = True
            self._t_first_audio = time.monotonic()
            self._tracer.mark(self._turn, "tts_first_audio")
            self._tracer.begin(self._turn, "player")
            ttft = _ms_since(self._t0)
            since_token = int((self._t_first_audio - self._t_first_token) * 1000) if self._got_first_token else 0
            log.info(f"TTS first audio  +{ttft}ms  (TTS latency {since_token}ms)")

        await self._player.send_chunk(audio_base64)

    async def _on_tts_done(self) -> None:
        """TTS finished -> append any DTMF tones, then signal player EOF."""
        if not self._active or not self._player:
            return

        self._tracer.end(self._turn, "tts")

        # Append DTMF tones after speech audio (populated in legacy path)
        for digit in self._dtmf_queue:
            audio = generate_dtmf_ulaw_b64(digit)
            await self._player.send_chunk(audio)
        self._dtmf_queue.clear()

        self._player.mark_tts_done()

    def _on_playback_done(self) -> None:
        """Player finished -> turn is complete."""
        if not self._active:
            return

        self._tracer.end(self._turn, "player")

        total = _ms_since(self._t0)
        log.info(f"Turn complete    +{total}ms total")

        self._active = False
        self._tts = None
        self._player = None

        if self._pending_hangup:
            self._pending_hangup = False
            self._emit(HangupRequestEvent())
        else:
            self._emit(AgentTurnDoneEvent())
