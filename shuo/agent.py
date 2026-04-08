"""
agent.py — Turn executor: LLM → TTS → playback.

Agent coordinates one agent response turn:
  1. Get a TTS connection from VoicePool
  2. Start the LLM with the user's transcript
  3. Stream tokens → TTS → AudioPlayer → phone
  4. Resolve turn outcome (DTMF? hangup? speech?)
  5. Emit AgentDoneEvent (or DTMFEvent / HangupEvent)

cancel_turn() aborts mid-flight and preserves history for the next turn.
"""

import asyncio
import time
from typing import Optional, Callable, List, Any

from .language import LanguageModel
from .voice import VoicePool, AudioPlayer, dtmf_tone
from .tracer import Tracer
from .telemetry import CallTelemetry, CP
from .log import ServiceLogger
from .call import (
    TurnOutcome,
    AgentDoneEvent, HoldStartEvent, HoldEndEvent,
    HangupPendingEvent, HangupEvent, DTMFEvent,
)

log = ServiceLogger("Agent")


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


class Agent:
    """
    Coordinates a single response pipeline: LLM → TTS → AudioPlayer.

    The LLM is persistent (history survives across turns).
    A fresh AudioPlayer is created per turn; TTS comes from VoicePool.
    """

    def __init__(
        self,
        phone,
        stream_sid:        str,
        emit:              Callable[[Any], None],
        voice_pool:        VoicePool,
        tracer:            Tracer,
        goal:              str = "",
        ctx:               Optional[Any] = None,   # Optional[CallContext]
        on_token_observed: Optional[Callable[[str], None]] = None,
        telemetry:         Optional[CallTelemetry] = None,
    ):
        self._phone            = phone
        self._stream_sid       = stream_sid
        self._emit             = emit
        self._voice_pool       = voice_pool
        self._tracer           = tracer
        self._telemetry        = telemetry
        self._on_token_observed = on_token_observed

        self._llm = LanguageModel(
            on_token=self._on_llm_token,
            on_done=self._on_llm_done,
            goal=goal,
            ctx=ctx,
            telemetry=telemetry,
        )

        self._tts:    Optional[object]      = None
        self._player: Optional[AudioPlayer] = None
        self._active: bool                  = False
        self._turn:   int                   = 0

        # Latency milestones
        self._t0:             float = 0.0
        self._t_first_token:  float = 0.0
        self._t_first_audio:  float = 0.0
        self._got_first_token: bool = False
        self._got_first_audio: bool = False

        # Per-turn accumulators (reset on start_turn)
        self._tts_had_text:      bool       = False
        self._pending_hangup:    bool       = False
        self._current_turn_text: str        = ""
        self._dtmf_queue:        List[str]  = []

        # Set when LLM decides to hang up — blocks new turns and barge-in cancellation
        self._hangup_decided:    bool       = False

    @property
    def is_turn_active(self) -> bool:
        return self._active

    @property
    def hangup_decided(self) -> bool:
        return self._hangup_decided

    @property
    def history(self) -> list:
        return self._llm.history

    def restore_history(self, saved_history: list, takeover_transcript: List[str]) -> Optional[str]:
        """
        Restore conversation history after human take-over hand-back.

        Returns a handback prompt for start_turn(), or None if no transcript
        (agent waits silently for the callee to speak first).
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

    # ── Turn lifecycle ──────────────────────────────────────────────

    async def start_turn(self, transcript: str, hold_check: bool = False) -> None:
        if self._hangup_decided:
            log.info("start_turn blocked: hangup already in progress")
            return
        if self._active:
            await self.cancel_turn()

        self._active           = True
        self._t0               = time.monotonic()
        self._got_first_token  = False
        self._got_first_audio  = False
        self._dtmf_queue       = []
        self._tts_had_text     = False
        self._pending_hangup   = False
        self._current_turn_text = ""

        self._turn = self._tracer.begin_turn(transcript)
        self._tracer.begin(self._turn, "tts_pool")

        self._tts = await self._voice_pool.get(
            on_audio=self._on_tts_audio,
            on_done=self._on_tts_done,
        )
        self._tracer.end(self._turn, "tts_pool")

        self._player = AudioPlayer(phone=self._phone, on_done=self._on_playback_done)

        message = (
            "[HOLD_CHECK] You are on hold. Transcription follows. "
            "If automated hold message \u2192 reply [HOLD_CONTINUE] only. "
            "If a real person is speaking \u2192 reply [HOLD_END] then respond normally.\n\n"
            f"Transcription: {transcript}"
        ) if hold_check else transcript

        self._tracer.begin(self._turn, "llm")
        await self._llm.start(message)
        log.info(f"Turn started  (TTS {_ms(self._t0)}ms setup)")

    async def cancel_turn(self) -> None:
        if not self._active:
            return
        elapsed    = _ms(self._t0) if self._t0 else 0
        self._active = False
        self._tracer.cancel_turn(self._turn)
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
        """Send a DTMF tone directly to the phone (dashboard manual control)."""
        await self._phone.send_audio(dtmf_tone(digit))

    async def cleanup(self) -> None:
        if self._active:
            await self.cancel_turn()

    # ── LLM callbacks ───────────────────────────────────────────────

    async def _on_llm_token(self, token: str) -> None:
        if not self._active or not self._tts:
            return

        if token:
            self._current_turn_text += token

        if token and self._llm.is_suppressed_token(token):
            log.debug(f"Suppressed function-call token from TTS: {token!r}")
            return

        if not self._got_first_token:
            self._got_first_token = True
            self._t_first_token   = time.monotonic()
            self._tracer.mark(self._turn, "llm_first_token")
            self._tracer.begin(self._turn, "tts")
            log.info(f"LLM first token  +{_ms(self._t0)}ms")

        if token:
            if not self._tts_had_text and self._telemetry:
                self._telemetry.checkpoint(CP.TTS_SYNTHESIS_START)
                self._telemetry.increment("tts_segments")
            self._tts_had_text = True
            await self._tts.send(token)
            if self._on_token_observed:
                asyncio.get_event_loop().call_soon(self._on_token_observed, token)

    async def _on_llm_done(self) -> None:
        if not self._active or not self._tts:
            return
        self._tracer.end(self._turn, "llm")
        outcome = self._llm.resolve_outcome(self._current_turn_text, self._tts_had_text)
        await self._dispatch_outcome(outcome)

    async def _dispatch_outcome(self, outcome: TurnOutcome) -> None:
        if outcome.hold_continue:
            await self._tts.cancel()
            self._tts    = None
            self._player = None
            self._active = False
            self._emit(AgentDoneEvent())
            return

        if outcome.emit_hold_start:
            self._emit(HoldStartEvent())
        if outcome.emit_hold_end:
            self._emit(HoldEndEvent())

        if outcome.hangup:
            self._hangup_decided = True
            self._pending_hangup = True
            self._emit(HangupPendingEvent())

        if outcome.dtmf_digits:
            if self._tts_had_text:
                log.debug("DTMF + text: suppressing spoken text, sending digit only")
            await self._tts.cancel()
            self._tts    = None
            self._player = None
            self._active = False
            self._emit(DTMFEvent(digits=outcome.dtmf_digits))
        elif outcome.has_speech:
            await self._tts.flush()
        else:
            await self._tts.cancel()
            self._tts    = None
            self._player = None
            self._active = False
            self._emit(AgentDoneEvent())

    # ── TTS / playback callbacks ────────────────────────────────────

    async def _on_tts_audio(self, audio_base64: str) -> None:
        if not self._active or not self._player:
            return
        if not self._got_first_audio:
            self._got_first_audio = True
            self._t_first_audio   = time.monotonic()
            self._tracer.mark(self._turn, "tts_first_audio")
            self._tracer.begin(self._turn, "player")
            if self._telemetry:
                self._telemetry.checkpoint(CP.TTS_FIRST_CHUNK)
            since_token = int((self._t_first_audio - self._t_first_token) * 1000) if self._got_first_token else 0
            log.info(f"TTS first audio  +{_ms(self._t0)}ms  (TTS latency {since_token}ms)")
        await self._player.send_chunk(audio_base64)

    async def _on_tts_done(self) -> None:
        if not self._active or not self._player:
            return
        self._tracer.end(self._turn, "tts")
        for digit in self._dtmf_queue:
            await self._player.send_chunk(dtmf_tone(digit))
        self._dtmf_queue.clear()
        self._player.mark_tts_done()

    def _on_playback_done(self) -> None:
        if not self._active:
            return
        self._tracer.end(self._turn, "player")
        if self._telemetry:
            self._telemetry.checkpoint(CP.TTS_PLAYBACK_DONE)
        log.info(f"Turn complete    +{_ms(self._t0)}ms total")
        self._active = False
        self._tts    = None
        self._player = None
        if self._pending_hangup:
            self._pending_hangup = False
            self._emit(HangupEvent())
        else:
            self._emit(AgentDoneEvent())
