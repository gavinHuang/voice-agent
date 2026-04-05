"""
Type definitions for shuo.

All state, events, and actions are immutable dataclasses.
Minimal -- only what the main loop needs to route decisions.

Conversation history lives in Agent, not in AppState.
"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional, Union, List


# =============================================================================
# STATE
# =============================================================================

class Phase(Enum):
    """Current phase of the conversation."""
    LISTENING = auto()    # Waiting for user / user speaking
    RESPONDING = auto()   # Agent active (LLM -> TTS -> Playback)
    HANGING_UP = auto()   # Hangup requested, waiting for call to end


@dataclass(frozen=True)
class AppState:
    """
    Application state -- just routing information.

    Conversation history is owned by Agent.
    Connection metadata (stream_sid) lives in CallSession (server) or
    as a local variable in run_conversation -- it is not needed by the
    state machine and was removed to keep AppState minimal.
    """
    phase: Phase = Phase.LISTENING
    hold_mode: bool = False


# =============================================================================
# TURN OUTCOME
# =============================================================================

@dataclass(frozen=True)
class TurnOutcome:
    """
    Result of resolving an LLM turn's side effects.

    Returned by _resolve_turn_outcome() (pure function in agent.py).
    Consumed by _dispatch_outcome() to drive TTS, events, and DTMF.

    Priority order for routing:
      hold_continue  → silent done (on-hold wait)
      dtmf_digits    → send digit, suppress speech
      has_speech     → flush TTS
      (else)         → silent empty turn
    """
    dtmf_digits: Optional[str] = None    # digit(s) to send, or None
    hold_continue: bool = False           # silent hold-wait — skip TTS, emit done
    emit_hold_start: bool = False         # emit HoldStartEvent
    emit_hold_end: bool = False           # emit HoldEndEvent
    hangup: bool = False                  # emit HangupPendingEvent
    has_speech: bool = False              # flush TTS (text was generated)


# =============================================================================
# EVENTS (inputs to the system)
# =============================================================================

@dataclass(frozen=True)
class StreamStartEvent:
    """Twilio stream started."""
    stream_sid: str
    call_sid: str = ""   # Twilio call SID (CA...) for REST API
    phone: str = ""      # Remote phone number (from TwiML custom parameter)


@dataclass(frozen=True)
class StreamStopEvent:
    """Twilio stream ended."""
    pass


@dataclass(frozen=True)
class MediaEvent:
    """Audio data received from Twilio."""
    audio_bytes: bytes


@dataclass(frozen=True)
class FluxStartOfTurnEvent:
    """Deepgram Flux detected user started speaking (barge-in)."""
    pass


@dataclass(frozen=True)
class FluxEndOfTurnEvent:
    """Deepgram Flux detected user finished speaking."""
    transcript: str


@dataclass(frozen=True)
class AgentTurnDoneEvent:
    """Agent finished speaking (playback complete)."""
    pass


@dataclass(frozen=True)
class HoldStartEvent:
    """Agent detected it is on hold — suppress barge-in until person returns."""
    pass


@dataclass(frozen=True)
class HoldEndEvent:
    """Agent detected a real person — exit hold mode, resume normal behaviour."""
    pass


@dataclass(frozen=True)
class HangupPendingEvent:
    """Agent detected [HANGUP] marker — block new turns while goodbye plays."""
    pass


@dataclass(frozen=True)
class HangupRequestEvent:
    """Agent finished its goodbye turn and wants to end the call."""
    pass


@dataclass(frozen=True)
class DTMFToneEvent:
    """Agent wants to send DTMF digits — handled by server via Twilio REST API."""
    digits: str   # e.g. "2" or "12" for a sequence


@dataclass(frozen=True)
class InitialGreetingEvent:
    """
    Synthetic event: triggers the opening agent turn when a call connects.

    Emitted by run_conversation after StreamStartEvent is processed (non-IVR,
    non-handback path). Routes through process_event so the LISTENING →
    RESPONDING transition is logged and auditable like any other transition.
    """
    opener: str


@dataclass(frozen=True)
class HandbackStartEvent:
    """
    Synthetic event: resumes the agent after a human supervisor take-over.

    Emitted by run_conversation when a saved handback prompt exists.
    Routes through process_event so the LISTENING → RESPONDING transition
    is visible to the state machine rather than being a direct bypass.
    """
    prompt: str


Event = Union[
    StreamStartEvent, StreamStopEvent, MediaEvent,
    FluxStartOfTurnEvent, FluxEndOfTurnEvent,
    AgentTurnDoneEvent,
    HoldStartEvent, HoldEndEvent,
    HangupPendingEvent, HangupRequestEvent,
    DTMFToneEvent,
    InitialGreetingEvent, HandbackStartEvent,
]


# =============================================================================
# ACTIONS (outputs from the system)
# =============================================================================

@dataclass(frozen=True)
class FeedFluxAction:
    """Send audio to Deepgram Flux."""
    audio_bytes: bytes


@dataclass(frozen=True)
class StartAgentTurnAction:
    """Start agent response pipeline."""
    transcript: str
    hold_check: bool = False


@dataclass(frozen=True)
class ResetAgentTurnAction:
    """Cancel agent response and clear Twilio buffer."""
    pass


Action = Union[
    FeedFluxAction,
    StartAgentTurnAction,
    ResetAgentTurnAction,
]
