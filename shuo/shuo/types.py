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


@dataclass(frozen=True)
class AppState:
    """
    Application state -- just routing information.

    Conversation history is owned by Agent, not tracked here.
    """
    phase: Phase = Phase.LISTENING
    stream_sid: Optional[str] = None
    hold_mode: bool = False


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


Event = Union[
    StreamStartEvent, StreamStopEvent, MediaEvent,
    FluxStartOfTurnEvent, FluxEndOfTurnEvent,
    AgentTurnDoneEvent,
    HoldStartEvent, HoldEndEvent,
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
