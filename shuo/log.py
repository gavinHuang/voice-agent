"""
log.py — Logging for voice-agent.

Shared utilities (colors, formatters, ServiceLogger) are imported from
core.log in dialact-eval (source of truth).

Logger — the call-lifecycle/event/action logger — stays here because it
depends on shuo.call event types (CallStartedEvent, AudioChunkEvent, etc.)
which are voice-agent specific.

Install dialact-eval in editable mode: pip install -e ../dialact-eval
"""
from typing import Optional

from core.log import (  # noqa: F401
    C,
    _c,
    _quote,
    ServiceLogger,
    ColorFormatter,
    CorrelatedFileFormatter,
    setup_logging,
    get_logger,
    set_log_call_id,
    clear_log_call_id,
)

import logging


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
