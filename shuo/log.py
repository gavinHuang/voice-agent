"""
log.py — Logging for voice-agent.

Shared utilities (C, ServiceLogger, setup_logging, etc.) live here as the
source of truth. dialact-eval/core/log.py re-exports from this module.

Logger — the call-lifecycle/event/action logger — is voice-agent specific
and depends on shuo.call event types.
"""
from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Optional

# ---------------------------------------------------------------------------
# Per-call log correlation
# ---------------------------------------------------------------------------

_call_id_var: ContextVar[str] = ContextVar("call_id", default="")


def set_log_call_id(call_id: str) -> None:
    """Tag all subsequent log lines in this async task with call_id."""
    _call_id_var.set(call_id)


def clear_log_call_id() -> None:
    """Remove the call_id tag (call finished)."""
    _call_id_var.set("")


# =============================================================================
# COLORS
# =============================================================================

class C:
    """ANSI color codes."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"


def _c(color: str, text: str) -> str:
    return color + text + C.RESET


def _quote(text: str, color: str = C.WHITE) -> str:
    return _c(color, '"' + text + '"')


# =============================================================================
# LOGGING SETUP
# =============================================================================

class ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ms = int(record.msecs)
        ts = self.formatTime(record, "%H:%M:%S") + f".{ms:03d}"
        time_str = _c(C.DIM, ts)
        call_id = _call_id_var.get()
        prefix = _c(C.DIM, f"[{call_id}] ") if call_id else ""
        return time_str + " \u2502 " + prefix + record.getMessage()


class CorrelatedFileFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        call_id = _call_id_var.get()
        if call_id:
            record = logging.makeLogRecord(record.__dict__)
            record.msg = f"[{call_id}] {record.getMessage()}"
            record.args = None
        return super().format(record)


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging for the application."""
    import os
    from logging.handlers import RotatingFileHandler

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(ColorFormatter())
    console.setLevel(level)

    file_formatter = CorrelatedFileFormatter(
        "%(asctime)s.%(msecs)03d | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log_dir = os.getenv("DATA_DIR", "")
    if log_dir:
        import pathlib
        pathlib.Path(log_dir).mkdir(parents=True, exist_ok=True)
        log_path = os.path.join(log_dir, "dialact_eval.log")
    else:
        log_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "dialact_eval.log",
        )
    file_handler = RotatingFileHandler(log_path, maxBytes=10 * 1024 * 1024, backupCount=3)
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [console, file_handler]

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# =============================================================================
# SERVICE LOGGER
# =============================================================================

class ServiceLogger:
    """Logger for individual services."""

    COLORS = {
        "LLM": C.BRIGHT_MAGENTA,
        "Translation": C.BRIGHT_CYAN,
        "Eval": C.BRIGHT_GREEN,
        "UI": C.BRIGHT_BLUE,
    }

    def __init__(self, service_name: str):
        self._logger = logging.getLogger("shuo." + service_name)
        self._name = service_name
        self._color = self.COLORS.get(service_name, C.WHITE)

    def connected(self) -> None:
        self._logger.info(
            _c(C.GREEN, "\u2713") + " " + _c(self._color, self._name) + " " + _c(C.DIM, "connected")
        )

    def disconnected(self) -> None:
        self._logger.debug(_c(C.DIM, "\u25CB " + self._name + " disconnected"))

    def cancelled(self) -> None:
        self._logger.debug(_c(C.DIM, "\u25CB " + self._name + " cancelled"))

    def error(self, msg: str, exc: Optional[Exception] = None) -> None:
        if exc:
            self._logger.error(
                _c(C.RED, "\u2717") + " " +
                _c(self._color, self._name + ":") + " " +
                msg + " " + _c(C.DIM, "(" + str(exc) + ")")
            )
        else:
            self._logger.error(
                _c(C.RED, "\u2717") + " " + _c(self._color, self._name + ":") + " " + msg
            )

    def warning(self, msg: str) -> None:
        self._logger.warning("  " + _c(C.YELLOW, self._name + ": " + msg))

    def debug(self, msg: str) -> None:
        self._logger.debug("  " + _c(C.DIM, self._name + ": " + msg))

    def info(self, msg: str) -> None:
        self._logger.info("  " + _c(self._color, self._name + ":") + " " + msg)


# =============================================================================
# CALL LIFECYCLE LOGGER  (voice-agent specific)
# =============================================================================


class Logger:
    """
    Unified logger for shuo call lifecycle events, actions, and transitions.

    Class methods  — lifecycle events (server, call, websocket, stream)
    Instance methods — event/action/transition logging in the conversation loop
    """

    _logger = logging.getLogger("shuo")

    # ── Lifecycle (class methods) ────────────────────────────────────

    @classmethod
    def server_starting(cls, port: int) -> None:
        cls._logger.info("\U0001F680 " + _c(C.CYAN, "Server starting on port " + str(port)))

    @classmethod
    def server_ready(cls, url: str) -> None:
        cls._logger.info(_c(C.GREEN, "\u2713  Ready") + " " + _c(C.DIM, url))

    @classmethod
    def call_initiating(cls, phone: str) -> None:
        cls._logger.info("\U0001F4DE " + _c(C.CYAN, "Calling " + phone + "..."))

    @classmethod
    def call_initiated(cls, sid: str) -> None:
        cls._logger.info(
            _c(C.GREEN, "\u2713  Call initiated") + " " + _c(C.DIM, "SID: " + sid[:8] + "...")
        )

    @classmethod
    def websocket_connected(cls) -> None:
        cls._logger.info("\U0001F50C " + _c(C.CYAN, "WebSocket connected"))

    @classmethod
    def websocket_disconnected(cls) -> None:
        cls._logger.info("\U0001F50C " + _c(C.DIM, "WebSocket disconnected"))

    @classmethod
    def shutdown(cls) -> None:
        cls._logger.info("\U0001F44B " + _c(C.DIM, "Shutting down"))

    # ── Instance methods (conversation loop) ─────────────────────────

    def __init__(self, verbose: bool = False):
        self._events_logger = logging.getLogger("shuo.events")
        self._verbose = verbose

    def event(self, event) -> None:
        """Log an incoming call event."""
        from .call import (
            AudioChunkEvent, CallStartedEvent, CallEndedEvent,
            UserSpokeEvent, UserSpeakingEvent, AgentDoneEvent,
            HoldStartEvent, HoldEndEvent,
        )

        if isinstance(event, AudioChunkEvent):
            if self._verbose:
                size = len(event.audio_bytes)
                self._events_logger.debug(_c(C.DIM, "\u2190 AudioChunk (" + str(size) + " bytes)"))
            return

        if isinstance(event, CallStartedEvent):
            self._events_logger.info(
                _c(C.GREEN, "\u25B6  Stream started") + " " +
                _c(C.DIM, "SID: " + event.stream_sid[:8] + "...")
            )
            return

        if isinstance(event, CallEndedEvent):
            self._events_logger.info("\u23F9  " + _c(C.DIM, "Stream stopped"))
            return

        if isinstance(event, UserSpokeEvent):
            text = event.transcript
            if len(text) > 60:
                text = text[:57] + "..."
            self._events_logger.info(
                _c(C.GREEN, "\u2190") + " " +
                _c(C.BRIGHT_BLUE, "STT") + " " +
                _c(C.GREEN, "EndOfTurn") + " " +
                _quote(text)
            )
            return

        if isinstance(event, UserSpeakingEvent):
            self._events_logger.info(
                _c(C.BRIGHT_RED, "\u26A1") + " " +
                _c(C.BRIGHT_BLUE, "STT") + " " +
                _c(C.BRIGHT_RED, "StartOfTurn") + " " +
                _c(C.DIM, "(barge-in)")
            )
            return

        if isinstance(event, AgentDoneEvent):
            self._events_logger.info(
                _c(C.GREEN, "\u2190") + " " +
                _c(C.DIM, "Agent turn done")
            )
            return

        if isinstance(event, HoldStartEvent):
            self._events_logger.info(
                "\u23F8  " + _c(C.YELLOW, "Hold mode") + " " +
                _c(C.DIM, "waiting for real person")
            )
            return

        if isinstance(event, HoldEndEvent):
            self._events_logger.info(
                "\u25B6  " + _c(C.GREEN, "Hold ended") + " " +
                _c(C.DIM, "real person detected")
            )
            return

    def action(self, action) -> None:
        """Log an outgoing call action."""
        from .call import StreamToSTTAction, StartTurnAction, CancelTurnAction

        if isinstance(action, StreamToSTTAction):
            if self._verbose:
                size = len(action.audio_bytes)
                self._events_logger.debug(_c(C.DIM, "\u2192 StreamToSTT (" + str(size) + " bytes)"))
            return

        if isinstance(action, StartTurnAction):
            msg = action.transcript
            if len(msg) > 40:
                msg = msg[:37] + "..."
            self._events_logger.info(
                _c(C.YELLOW, "\u2192") + " " +
                _c(C.YELLOW, "Start") + " " +
                _c(C.BRIGHT_CYAN, "Agent") + " " +
                _quote(msg, C.DIM)
            )
            return

        if isinstance(action, CancelTurnAction):
            self._events_logger.info(
                _c(C.YELLOW, "\u2192") + " " +
                _c(C.BRIGHT_RED, "Reset") + " " +
                _c(C.BRIGHT_CYAN, "Agent")
            )
            return

    def transition(self, old_phase, new_phase) -> None:
        """Log a phase transition."""
        if old_phase != new_phase:
            self._events_logger.info(
                _c(C.MAGENTA, "\u25C6") + " " +
                _c(C.DIM, old_phase.name) + " " +
                _c(C.MAGENTA, "\u2192") + " " +
                _c(C.BRIGHT_MAGENTA, new_phase.name)
            )

    def error(self, msg: str, exc: Optional[Exception] = None) -> None:
        if exc:
            self._events_logger.error(
                _c(C.RED, "\u2717 " + msg + ":") + " " + _c(C.DIM, str(exc))
            )
        else:
            self._events_logger.error(_c(C.RED, "\u2717 " + msg))
