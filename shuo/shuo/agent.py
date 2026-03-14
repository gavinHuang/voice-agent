"""
Agent -- self-contained LLM -> TTS -> Player pipeline.

Encapsulates the entire agent response lifecycle.
Owns conversation history across turns.

    start_turn(transcript)          -> add to history -> LLM -> TTS -> Player -> Twilio
    start_turn(transcript, hold_check=True) -> LLM hold-check -> emit Hold events
    cancel_turn()                   -> cancel all, keep history

TTS connections are managed by TTSPool (see services/tts_pool.py).

Marker protocol (embedded in LLM output, stripped before TTS):
    [DTMF:N]         -- dial digit N; tone appended after TTS audio
    [HOLD]           -- agent is entering hold mode
    [HOLD_CONTINUE]  -- still on hold; suppress TTS, end turn silently
    [HOLD_END]       -- real person detected; exit hold, speak normally
"""

import asyncio
import json
import time
from typing import Optional, Callable, List, Dict, Any

from fastapi import WebSocket

from .services.llm import LLMService
from .services.tts import create_tts
from .services.tts_pool import TTSPool
from .services.player import AudioPlayer
from .services.dtmf import generate_dtmf_ulaw_b64
from .tracer import Tracer
from .log import ServiceLogger
from .types import AgentTurnDoneEvent, HoldStartEvent, HoldEndEvent, HangupPendingEvent, HangupRequestEvent

log = ServiceLogger("Agent")


def _ms_since(t0: float) -> int:
    """Milliseconds elapsed since t0."""
    return int((time.monotonic() - t0) * 1000)


# =============================================================================
# MARKER SCANNER
# =============================================================================

class MarkerScanner:
    """
    Strips [DTMF:N] / [HOLD*] markers from streaming LLM text.

    Feed tokens one at a time; receives (clean_text, markers) back.
    Buffers partial markers across token boundaries.

    Known markers:
        HOLD, HOLD_END, HOLD_CONTINUE
        DTMF:0 ... DTMF:9, DTMF:*, DTMF:#
    """

    KNOWN = {"HOLD", "HOLD_END", "HOLD_CONTINUE", "HANGUP"}
    MAX_BUF = 20  # Max chars to buffer before giving up on a potential marker

    def __init__(self) -> None:
        self._buf = ""
        self._in_marker = False

    def feed(self, token: str) -> tuple[str, list[str]]:
        """Process one token. Returns (clean_text, list_of_markers)."""
        clean = ""
        markers: list[str] = []

        for ch in token:
            if not self._in_marker:
                if ch == "[":
                    self._in_marker = True
                    self._buf = ""
                else:
                    clean += ch
            else:
                if ch == "]":
                    inner = self._buf
                    if inner in self.KNOWN or self._is_dtmf(inner):
                        markers.append(inner)
                        # Marker stripped — nothing added to clean
                    else:
                        # Not a recognised marker; emit as literal text
                        clean += "[" + inner + "]"
                    self._in_marker = False
                    self._buf = ""
                elif len(self._buf) < self.MAX_BUF:
                    self._buf += ch
                else:
                    # Buffer overflow — not a valid marker, flush as literal text
                    clean += "[" + self._buf + ch
                    self._in_marker = False
                    self._buf = ""

        return clean, markers

    def flush(self) -> str:
        """Flush any remaining buffered content at end of stream."""
        if self._in_marker:
            remaining = "[" + self._buf
            self._in_marker = False
            self._buf = ""
            return remaining
        return ""

    @staticmethod
    def _is_dtmf(inner: str) -> bool:
        return len(inner) == 6 and inner.startswith("DTMF:") and inner[5] in "0123456789*#"


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
        websocket: WebSocket,
        stream_sid: str,
        emit: Callable[[Any], None],
        tts_pool: TTSPool,
        tracer: Tracer,
        goal: str = "",
        on_token_observed: Optional[Callable[[str], None]] = None,
    ):
        self._websocket = websocket
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

        # Per-turn marker state
        self._scanner = MarkerScanner()
        self._dtmf_queue: List[str] = []
        self._tts_had_text: bool = False
        self._pending_hold_start: bool = False
        self._pending_hold_end: bool = False
        self._pending_hangup: bool = False

    @property
    def is_turn_active(self) -> bool:
        return self._active

    @property
    def history(self) -> List[Dict[str, str]]:
        """Read-only access to conversation history (owned by LLM)."""
        return self._llm.history

    def restore_history(
        self,
        saved_history: List[Dict[str, str]],
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

        # Reset per-turn marker state
        self._scanner = MarkerScanner()
        self._dtmf_queue = []
        self._tts_had_text = False
        self._pending_hold_start = False
        self._pending_hold_end = False
        self._pending_hangup = False

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
            websocket=self._websocket,
            stream_sid=self._stream_sid,
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

        Generates the tone as μ-law audio and sends it directly to Twilio,
        where it is played to the remote party — the same path used by the
        agent's own [DTMF:N] marker mechanism.
        """
        audio = generate_dtmf_ulaw_b64(digit)
        msg = json.dumps({
            "event": "media",
            "streamSid": self._stream_sid,
            "media": {"payload": audio},
        })
        await self._websocket.send_text(msg)

    async def cleanup(self) -> None:
        """Final cleanup when call ends."""
        if self._active:
            await self.cancel_turn()

    # ── Internal Callbacks ──────────────────────────────────────────

    async def _on_llm_token(self, token: str) -> None:
        """LLM produced a token -> scan for markers, feed clean text to TTS."""
        if not self._active or not self._tts:
            return

        if not self._got_first_token:
            self._got_first_token = True
            self._t_first_token = time.monotonic()
            self._tracer.mark(self._turn, "llm_first_token")
            self._tracer.begin(self._turn, "tts")
            log.info(f"⏱  LLM first token  +{_ms_since(self._t0)}ms")

        clean_text, markers = self._scanner.feed(token)

        for m in markers:
            if m.startswith("DTMF:"):
                self._dtmf_queue.append(m[5:])
            elif m == "HOLD":
                self._pending_hold_start = True
            elif m == "HOLD_END":
                self._pending_hold_end = True
            elif m == "HANGUP":
                self._pending_hangup = True
                self._emit(HangupPendingEvent())  # Block new turns immediately
            # HOLD_CONTINUE is silently absorbed — no TTS, stay in hold

        if clean_text:
            self._tts_had_text = True
            await self._tts.send(clean_text)
            if self._on_token_observed:
                self._on_token_observed(clean_text)

    async def _on_llm_done(self) -> None:
        """LLM finished -> flush scanner, fire hold events, flush TTS."""
        if not self._active or not self._tts:
            return

        self._tracer.end(self._turn, "llm")

        # Flush any partial marker buffer
        remaining = self._scanner.flush()
        if remaining:
            self._tts_had_text = True
            await self._tts.send(remaining)

        # Fire hold state events
        if self._pending_hold_start:
            self._emit(HoldStartEvent())
            self._pending_hold_start = False

        if self._pending_hold_end:
            self._emit(HoldEndEvent())
            self._pending_hold_end = False

        if self._tts_had_text:
            # Normal path: flush TTS, playback will trigger AgentTurnDoneEvent
            await self._tts.flush()
        else:
            # HOLD_CONTINUE: nothing to say — skip TTS, end turn immediately
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
            log.info(f"⏱  TTS first audio  +{ttft}ms  (TTS latency {since_token}ms)")

        await self._player.send_chunk(audio_base64)

    async def _on_tts_done(self) -> None:
        """TTS finished -> append any DTMF tones, then signal player EOF."""
        if not self._active or not self._player:
            return

        self._tracer.end(self._turn, "tts")

        # Append DTMF tones after speech audio
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
        log.info(f"⏱  Turn complete    +{total}ms total")

        self._active = False
        self._tts = None
        self._player = None

        if self._pending_hangup:
            self._pending_hangup = False
            self._emit(HangupRequestEvent())
        else:
            self._emit(AgentTurnDoneEvent())
