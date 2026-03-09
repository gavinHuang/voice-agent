"""
Pure state machine for shuo.

The process_event function is the heart of the system:
    (State, Event) -> (State, List[Action])

With Deepgram Flux handling turn detection, this is now
a trivial conversation controller (~30 lines of logic).
"""

from dataclasses import replace
from typing import List, Tuple

from .types import (
    AppState, Phase,
    Event, StreamStartEvent, StreamStopEvent, MediaEvent,
    FluxStartOfTurnEvent, FluxEndOfTurnEvent, AgentTurnDoneEvent,
    HoldStartEvent, HoldEndEvent, HangupPendingEvent, HangupRequestEvent,
    Action, FeedFluxAction, StartAgentTurnAction, ResetAgentTurnAction,
)


def process_event(state: AppState, event: Event) -> Tuple[AppState, List[Action]]:
    """
    Pure state machine: (State, Event) -> (State, Actions)

    With Flux, this is just a simple router:
    - MediaEvent        -> feed audio to Flux
    - FluxEndOfTurnEvent -> start agent response
    - FluxStartOfTurnEvent -> interrupt (barge-in)
    - AgentTurnDoneEvent -> back to listening
    """
    # Once hanging up, ignore everything except stream stop
    if state.phase == Phase.HANGING_UP:
        if isinstance(event, StreamStopEvent):
            return state, []
        return state, []

    if isinstance(event, StreamStartEvent):
        return replace(state, stream_sid=event.stream_sid, phase=Phase.LISTENING), []

    if isinstance(event, StreamStopEvent):
        actions: List[Action] = []
        if state.phase == Phase.RESPONDING:
            actions.append(ResetAgentTurnAction())
        return state, actions

    if isinstance(event, MediaEvent):
        return state, [FeedFluxAction(audio_bytes=event.audio_bytes)]

    if isinstance(event, FluxEndOfTurnEvent):
        if event.transcript and state.phase == Phase.LISTENING:
            new_state = replace(state, phase=Phase.RESPONDING)
            return new_state, [StartAgentTurnAction(
                transcript=event.transcript,
                hold_check=state.hold_mode,
            )]
        return state, []

    if isinstance(event, FluxStartOfTurnEvent):
        if state.phase == Phase.RESPONDING and not state.hold_mode:
            return replace(state, phase=Phase.LISTENING), [ResetAgentTurnAction()]
        return state, []

    if isinstance(event, AgentTurnDoneEvent):
        if state.phase == Phase.RESPONDING:
            return replace(state, phase=Phase.LISTENING), []
        return state, []

    if isinstance(event, HoldStartEvent):
        return replace(state, hold_mode=True), []

    if isinstance(event, HoldEndEvent):
        return replace(state, hold_mode=False), []

    if isinstance(event, (HangupPendingEvent, HangupRequestEvent)):
        return replace(state, phase=Phase.HANGING_UP), []

    return state, []
