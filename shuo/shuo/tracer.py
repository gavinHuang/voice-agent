"""
Lightweight span tracer for shuo.

Records begin/end spans and point-in-time markers for each agent turn.
Persists as JSON to /tmp/shuo/<call_id>.json on call end.

Usage:
    tracer = Tracer()
    tracer.begin_turn(1, "Hello, how are you?")
    tracer.begin(1, "llm")
    tracer.mark(1, "llm_first_token")
    tracer.end(1, "llm")
    tracer.save("MZ8a3b1f")  # -> /tmp/shuo/MZ8a3b1f.json
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict

from .log import get_logger

logger = get_logger("shuo.tracer")

TRACE_DIR = Path("/tmp/shuo")


@dataclass
class Span:
    """A named time range within a turn."""
    name: str
    start_ms: float
    end_ms: Optional[float] = None


@dataclass
class Marker:
    """A named point-in-time within a turn."""
    name: str
    time_ms: float


@dataclass
class Turn:
    """All trace data for a single agent turn."""
    turn_number: int
    transcript: str = ""
    t0: float = 0.0  # monotonic reference (not serialized)
    spans: List[Span] = field(default_factory=list)
    markers: List[Marker] = field(default_factory=list)
    cancelled: bool = False


class Tracer:
    """
    Records spans and markers for each agent turn.

    All timestamps are stored as milliseconds relative to the turn's t0.
    """

    def __init__(self) -> None:
        self._turns: Dict[int, Turn] = {}
        self._turn_counter = 0

    def begin_turn(self, transcript: str) -> int:
        """Start a new turn, returns turn number."""
        self._turn_counter += 1
        turn = Turn(
            turn_number=self._turn_counter,
            transcript=transcript,
            t0=time.monotonic(),
        )
        self._turns[self._turn_counter] = turn
        return self._turn_counter

    def begin(self, turn: int, name: str) -> None:
        """Begin a named span."""
        t = self._turns.get(turn)
        if not t:
            return
        ms = (time.monotonic() - t.t0) * 1000
        t.spans.append(Span(name=name, start_ms=ms))

    def end(self, turn: int, name: str) -> None:
        """End a named span."""
        t = self._turns.get(turn)
        if not t:
            return
        ms = (time.monotonic() - t.t0) * 1000
        # Find the last span with this name that hasn't been ended
        for span in reversed(t.spans):
            if span.name == name and span.end_ms is None:
                span.end_ms = ms
                return

    def mark(self, turn: int, name: str) -> None:
        """Record a point-in-time marker."""
        t = self._turns.get(turn)
        if not t:
            return
        ms = (time.monotonic() - t.t0) * 1000
        t.markers.append(Marker(name=name, time_ms=ms))

    def cancel_turn(self, turn: int) -> None:
        """Mark turn as cancelled and end all open spans at current time."""
        t = self._turns.get(turn)
        if not t:
            return
        t.cancelled = True
        ms = (time.monotonic() - t.t0) * 1000
        for span in t.spans:
            if span.end_ms is None:
                span.end_ms = ms

    def save(self, call_id: str) -> Optional[Path]:
        """Write trace data to /tmp/shuo/<call_id>.json."""
        if not self._turns:
            return None

        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        path = TRACE_DIR / f"{call_id}.json"

        data = {
            "call_id": call_id,
            "turns": [
                {
                    "turn": t.turn_number,
                    "transcript": t.transcript,
                    "cancelled": t.cancelled,
                    "spans": [asdict(s) for s in t.spans],
                    "markers": [asdict(m) for m in t.markers],
                }
                for t in sorted(self._turns.values(), key=lambda x: x.turn_number)
            ],
        }

        path.write_text(json.dumps(data, indent=2))
        logger.info(f"Trace saved to {path}")
        return path


def cleanup_traces(
    max_files: int | None = None,
    max_age_hours: float | None = None,
) -> int:
    """Remove old trace files from TRACE_DIR. Returns number of files deleted.

    Args:
        max_files: Maximum number of trace files to keep (default from
            TRACE_MAX_FILES env var, fallback 100).
        max_age_hours: Maximum age in hours (default from TRACE_MAX_AGE_HOURS
            env var, fallback 24).

    Applies age filter first, then caps total count by removing oldest first.
    """
    if max_files is None:
        max_files = int(os.getenv("TRACE_MAX_FILES", "100"))
    if max_age_hours is None:
        max_age_hours = float(os.getenv("TRACE_MAX_AGE_HOURS", "24"))

    if not TRACE_DIR.exists():
        return 0

    deleted = 0
    now = time.time()
    cutoff = now - (max_age_hours * 3600)

    # Phase 1: Delete files older than max_age_hours
    traces = list(TRACE_DIR.glob("*.json"))
    for p in traces:
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                deleted += 1
        except OSError:
            pass

    # Phase 2: If still over max_files, delete oldest first
    remaining = sorted(TRACE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    over = len(remaining) - max_files
    if over > 0:
        for p in remaining[:over]:
            try:
                p.unlink()
                deleted += 1
            except OSError:
                pass

    if deleted:
        logger.info(f"Trace cleanup: removed {deleted} file(s)")
    return deleted
