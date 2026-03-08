"""
The main event loop for shuo.

This is the explicit, readable loop that drives the entire system:

    while connected:
        event = receive()                               # I/O (from queue)
        state, actions = process_event(state, event)    # PURE
        for action in actions:
            dispatch(action)                            # I/O

Events come from:
- Twilio WebSocket (audio packets)
- Deepgram Flux (turn events)
- Agent (playback complete)
"""

import json
import os
import asyncio
from dataclasses import replace
from typing import Callable, Optional

from fastapi import WebSocket

from .types import (
    AppState, Phase,
    Event, StreamStartEvent, StreamStopEvent,
    FluxStartOfTurnEvent, FluxEndOfTurnEvent,
    AgentTurnDoneEvent, HoldStartEvent, HoldEndEvent, HangupRequestEvent,
    FeedFluxAction, StartAgentTurnAction, ResetAgentTurnAction,
)
from .state import process_event
from .services.flux import FluxService
from .services.tts_pool import TTSPool
from .services.twilio_client import parse_twilio_message
from .agent import Agent
from .tracer import Tracer
from .log import Logger, get_logger

logger = get_logger("shuo.conversation")


async def run_conversation_over_twilio(
    websocket: WebSocket,
    observer: Optional[Callable[[dict], None]] = None,
    should_suppress_agent: Optional[Callable[[], bool]] = None,
    on_agent_ready: Optional[Callable[["Agent"], None]] = None,
    get_goal: Optional[Callable[[str], str]] = None,
    on_hangup: Optional[Callable[[], None]] = None,
) -> None:
    """
    Main event loop for a single call.

    1. Create shared event queue
    2. Create Flux service (always-on STT + turn detection)
    3. Start Twilio reader
    4. On StreamStart, create Agent
    5. Process events through pure state machine
    6. Dispatch actions inline
    """
    event_log = Logger(verbose=False)
    event_queue: asyncio.Queue[Event] = asyncio.Queue()
    tracer = Tracer()

    agent: Optional[Agent] = None
    tts_pool = TTSPool(pool_size=1, ttl=8.0)
    stream_sid: Optional[str] = None

    # ── Flux Callbacks (push events to queue) ───────────────────────

    async def on_flux_end_of_turn(transcript: str) -> None:
        await event_queue.put(FluxEndOfTurnEvent(transcript=transcript))

    async def on_flux_start_of_turn() -> None:
        await event_queue.put(FluxStartOfTurnEvent())

    # ── Create Flux Service ─────────────────────────────────────────

    flux = FluxService(
        on_end_of_turn=on_flux_end_of_turn,
        on_start_of_turn=on_flux_start_of_turn,
    )

    # ── Twilio WebSocket Reader ─────────────────────────────────────

    async def read_twilio() -> None:
        """Background task to read from Twilio and push to event queue."""
        try:
            while True:
                raw = await websocket.receive_text()
                data = json.loads(raw)
                event = parse_twilio_message(data)
                if event:
                    await event_queue.put(event)
                    if isinstance(event, StreamStopEvent):
                        break
        except Exception as e:
            event_log.error("Twilio reader", e)
            await event_queue.put(StreamStopEvent())

    # ── Initialize ──────────────────────────────────────────────────

    state = AppState()
    reader_task = asyncio.create_task(read_twilio())

    try:
        while True:
            # ─── RECEIVE ────────────────────────────────────────────
            event = await event_queue.get()

            event_log.event(event)

            # Initialize services on stream start
            if isinstance(event, StreamStartEvent):
                stream_sid = event.stream_sid
                await flux.start()
                await tts_pool.start()
                goal = get_goal(event.call_sid) if get_goal else os.getenv("CALL_GOAL", "")
                agent = Agent(
                    websocket=websocket,
                    stream_sid=event.stream_sid,
                    emit=lambda e: event_queue.put_nowait(e),
                    tts_pool=tts_pool,
                    tracer=tracer,
                    goal=goal,
                    on_token_observed=(
                        (lambda tok: observer({"type": "agent_token", "token": tok}))
                        if observer else None
                    ),
                )
                if on_agent_ready:
                    on_agent_ready(agent)

            # ─── UPDATE (pure) ──────────────────────────────────────
            old_phase = state.phase
            state, actions = process_event(state, event)
            event_log.transition(old_phase, state.phase)

            # ─── OBSERVE ────────────────────────────────────────────
            if observer:
                if isinstance(event, StreamStartEvent):
                    observer({
                        "type":       "stream_start",
                        "call_sid":   event.call_sid,
                        "stream_sid": event.stream_sid,
                        "phone":      event.phone,
                    })
                elif isinstance(event, FluxEndOfTurnEvent) and event.transcript:
                    observer({"type": "transcript", "text": event.transcript})
                elif isinstance(event, AgentTurnDoneEvent):
                    observer({"type": "agent_done"})
                elif isinstance(event, HoldStartEvent):
                    observer({"type": "hold"})
                elif isinstance(event, HoldEndEvent):
                    observer({"type": "hold_end"})
                elif isinstance(event, StreamStopEvent):
                    observer({"type": "stream_stop"})
                if old_phase != state.phase:
                    observer({"type": "phase_change", "from": old_phase.name, "to": state.phase.name})

            # ─── DISPATCH (side effects) ────────────────────────────
            for action in actions:
                event_log.action(action)
                if isinstance(action, FeedFluxAction):
                    await flux.send(action.audio_bytes)

                elif isinstance(action, StartAgentTurnAction):
                    if agent and not (should_suppress_agent and should_suppress_agent()):
                        await agent.start_turn(action.transcript, hold_check=action.hold_check)

                elif isinstance(action, ResetAgentTurnAction):
                    if agent:
                        await agent.cancel_turn()

            # ─── INITIAL GREETING ────────────────────────────────────
            if isinstance(event, StreamStartEvent):
                initial_msg = os.getenv("INITIAL_MESSAGE", "").strip()
                opener = initial_msg or (
                    "[CALL_STARTED]" if goal else ""
                )
                if opener and agent:
                    state = replace(state, phase=Phase.RESPONDING)
                    await agent.start_turn(opener)

            # ─── EXIT CHECK ─────────────────────────────────────────
            if isinstance(event, StreamStopEvent):
                break

            if isinstance(event, HangupRequestEvent):
                if on_hangup:
                    on_hangup()
                if observer:
                    observer({"type": "stream_stop"})
                try:
                    await websocket.close()
                except Exception:
                    pass
                break

    except Exception as e:
        event_log.error("Call loop", e)
        raise

    finally:
        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass

        if agent:
            await agent.cleanup()

        await tts_pool.stop()
        await flux.stop()

        # Save trace
        call_id = stream_sid or "unknown"
        tracer.save(call_id)

        Logger.websocket_disconnected()
