"""
telemetry.py — Per-call telemetry meter collection.

Records named checkpoints and counters throughout a call's lifecycle,
then produces a structured summary at call end.

Usage:
    tel = CallTelemetry()
    tel.checkpoint(CP.CALL_CONNECTED)
    tel.increment("llm_turns")
    summary = tel.summary()  # -> {"checkpoints": {...}, "durations": {...}, "counters": {...}}
"""

import time
from typing import Dict, List, Optional, Tuple

from .log import get_logger

logger = get_logger("shuo.telemetry")


# =============================================================================
# CHECKPOINT CONSTANTS
# =============================================================================

class CP:
    """Canonical checkpoint name constants."""
    SCRIPT_GENERATION_START = "script_generation_start"
    SCRIPT_GENERATION_END   = "script_generation_end"
    CALL_DIAL               = "call_dial"
    CALL_CONNECTED          = "call_connected"
    STT_READY               = "stt_ready"
    STT_FIRST_RESULT        = "stt_first_result"
    LLM_START               = "llm_start"
    LLM_FIRST_TOKEN         = "llm_first_token"
    LLM_END                 = "llm_end"
    TTS_SYNTHESIS_START     = "tts_synthesis_start"
    TTS_FIRST_CHUNK         = "tts_first_chunk"
    TTS_PLAYBACK_DONE       = "tts_playback_done"
    HANGUP                  = "hangup"


# Checkpoints that should appear in every call; absence triggers a warning.
_REQUIRED_CHECKPOINTS: List[str] = [
    CP.CALL_CONNECTED,
    CP.STT_READY,
    CP.LLM_START,
    CP.LLM_FIRST_TOKEN,
    CP.TTS_FIRST_CHUNK,
    CP.HANGUP,
]

# Duration pairs: (label, from_checkpoint, to_checkpoint)
_DURATION_PAIRS: List[Tuple[str, str, str]] = [
    ("dial_to_connect_ms",              CP.CALL_DIAL,               CP.CALL_CONNECTED),
    ("connect_to_stt_ready_ms",         CP.CALL_CONNECTED,          CP.STT_READY),
    ("connect_to_first_word_ms",        CP.CALL_CONNECTED,          CP.STT_FIRST_RESULT),
    ("llm_ttft_ms",                     CP.LLM_START,               CP.LLM_FIRST_TOKEN),
    ("user_spoke_to_first_audio_ms",    CP.STT_FIRST_RESULT,        CP.TTS_FIRST_CHUNK),
    ("tts_synthesis_to_first_chunk_ms", CP.TTS_SYNTHESIS_START,     CP.TTS_FIRST_CHUNK),
    ("script_generation_ms",            CP.SCRIPT_GENERATION_START, CP.SCRIPT_GENERATION_END),
    ("total_call_ms",                   CP.CALL_CONNECTED,          CP.HANGUP),
]


# =============================================================================
# CALL TELEMETRY
# =============================================================================

class CallTelemetry:
    """
    Collects named checkpoints and counters for one call.

    Checkpoints are recorded once; duplicates are ignored with a warning.
    Counters accumulate across the full call (e.g. total LLM turns).

    summary() returns a structured dict with:
      - checkpoints: ms offsets relative to call_connected
      - durations:   computed ms between key checkpoint pairs
      - counters:    raw accumulated counts
    """

    def __init__(self) -> None:
        self._checkpoints: Dict[str, float] = {}   # name -> monotonic timestamp
        self._counters:    Dict[str, int]   = {}

    # ── Public API ──────────────────────────────────────────────────────

    def checkpoint(self, name: str) -> None:
        """Record a named checkpoint at the current time (first call wins)."""
        if name in self._checkpoints:
            logger.warning(f"Duplicate telemetry checkpoint ignored: {name!r}")
            return
        self._checkpoints[name] = time.monotonic()

    def increment(self, name: str, amount: int = 1) -> None:
        """Increment a named counter."""
        self._counters[name] = self._counters.get(name, 0) + amount

    def summary(self) -> dict:
        """
        Produce a structured call summary.

        All checkpoint timestamps are expressed as ms elapsed since
        call_connected (or as raw monotonic seconds if call_connected
        was never recorded).
        """
        t0: Optional[float] = self._checkpoints.get(CP.CALL_CONNECTED)

        # Relative timestamps
        timestamps: Dict[str, Optional[float]] = {}
        for name, ts in self._checkpoints.items():
            if t0 is not None:
                timestamps[f"{name}_ms"] = round((ts - t0) * 1000, 1)
            else:
                timestamps[f"{name}_ms"] = round(ts * 1000, 1)

        # Computed durations
        durations: Dict[str, float] = {}
        for label, from_cp, to_cp in _DURATION_PAIRS:
            if from_cp in self._checkpoints and to_cp in self._checkpoints:
                durations[label] = round(
                    (self._checkpoints[to_cp] - self._checkpoints[from_cp]) * 1000, 1
                )

        # Warn on missing required checkpoints
        missing = [cp for cp in _REQUIRED_CHECKPOINTS if cp not in self._checkpoints]
        if missing:
            logger.warning(f"Call telemetry: missing checkpoints: {missing}")

        return {
            "checkpoints": timestamps,
            "durations":   durations,
            "counters":    dict(self._counters),
        }
