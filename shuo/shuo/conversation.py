"""
The main event loop for shuo.

This is the explicit, readable loop that drives the entire system:

    while connected:
        event = receive()                               # I/O (from queue)
        state, actions = process_event(state, event)    # PURE
        for action in actions:
            dispatch(action)                            # I/O

Events come from:
- ISP (audio packets, stream start/stop)
- Deepgram Flux (turn events)
- Agent (playback complete)

All state transitions — including the initial greeting and post-takeover
handback — go through process_event via InitialGreetingEvent and
HandbackStartEvent. There are no direct state mutations in this file.
"""

import os
import asyncio
from typing import Awaitable, Callable, Optional

from .types import (
    AppState, Phase,
    Event, Action,
    StreamStartEvent, StreamStopEvent,
    FluxStartOfTurnEvent, FluxEndOfTurnEvent,
    AgentTurnDoneEvent, HoldStartEvent, HoldEndEvent, HangupRequestEvent,
    DTMFToneEvent, MediaEvent,
    InitialGreetingEvent, HandbackStartEvent,
    FeedFluxAction, StartAgentTurnAction, ResetAgentTurnAction,
)
from .state import process_event
from .services.flux import FluxService
from .services.flux_pool import FluxPool
from .services.tts_pool import TTSPool
from .agent import Agent
from .tracer import Tracer
from .log import Logger, get_logger

logger = get_logger("shuo.conversation")

CALL_INACTIVITY_TIMEOUT = float(os.getenv("CALL_INACTIVITY_TIMEOUT", "300"))


async def _inactivity_watchdog(
    event_queue: asyncio.Queue,
    timeout: float,
    last_activity: Optional[list] = None,  # mutable single-element list [float] for shared state
) -> None:
    """Hang up if no meaningful call activity for `timeout` seconds.

    Activity events (StreamStart, FluxEndOfTurn, AgentTurnDone, HoldStart, HoldEnd)
    update last_activity[0]. MediaEvents do NOT count — a silent-but-connected call
    streaming audio silence should still be caught.
    """
    if last_activity is None:
        last_activity = [asyncio.get_event_loop().time()]
    try:
        while True:
            await asyncio.sleep(min(5.0, timeout))
            now = asyncio.get_event_loop().time()
            if now - last_activity[0] > timeout:
                logger.warning(f"Inactivity timeout ({timeout}s) — requesting hangup")
                await event_queue.put(HangupRequestEvent())
                return
    except asyncio.CancelledError:
        pass


async def run_conversation(
    isp,
    observer: Optional[Callable[[dict], None]] = None,
    should_suppress_agent: Optional[Callable[[], bool]] = None,
    on_agent_ready: Optional[Callable[["Agent"], None]] = None,
    get_goal: Optional[Callable[[str], str]] = None,
    on_hangup: Optional[Callable[[], None]] = None,
    get_saved_state: Optional[Callable[[str], "Awaitable[Optional[dict]]"]] = None,
    tts_pool: Optional[TTSPool] = None,
    flux_pool: Optional[FluxPool] = None,
    ivr_mode: Optional[Callable[[], bool]] = None,
    on_dtmf: Optional[Callable[[str], None]] = None,
) -> None:
    """
    Main event loop for a single call.

    1. Create shared event queue
    2. Create Flux service (always-on STT + turn detection)
    3. Start ISP (registers callbacks, begins reading stream)
    4. On StreamStart, create Agent
    5. Process events through pure state machine
    6. Dispatch actions via local dispatch() coroutine
    """
    event_log = Logger(verbose=False)
    event_queue: asyncio.Queue[Event] = asyncio.Queue()
    tracer = Tracer()

    agent: Optional[Agent] = None
    flux: Optional[FluxService] = None
    _own_tts_pool = tts_pool is None   # True if we created it locally
    if _own_tts_pool:
        tts_pool = TTSPool(pool_size=1, ttl=8.0)
    stream_sid: Optional[str] = None

    # ── Flux Callbacks (push events to queue) ───────────────────────

    async def on_flux_end_of_turn(transcript: str) -> None:
        await event_queue.put(FluxEndOfTurnEvent(transcript=transcript))

    async def on_flux_start_of_turn() -> None:
        await event_queue.put(FluxStartOfTurnEvent())

    async def on_flux_dead() -> None:
        logger.error("STT permanently unavailable — hanging up")
        await event_queue.put(HangupRequestEvent())

    # ── ISP Callbacks (push events to queue) ────────────────────────

    async def on_isp_media(audio_bytes: bytes) -> None:
        await event_queue.put(MediaEvent(audio_bytes=audio_bytes))

    async def on_isp_start(stream_sid_: str, call_sid: str, phone: str) -> None:
        await event_queue.put(StreamStartEvent(stream_sid=stream_sid_, call_sid=call_sid, phone=phone))

    async def on_isp_stop() -> None:
        await event_queue.put(StreamStopEvent())

    # ── Action Dispatcher ────────────────────────────────────────────
    # Single coroutine for all action dispatch — used both in the main loop
    # and when processing synthetic events (InitialGreetingEvent, HandbackStartEvent).

    async def dispatch(action: Action) -> None:
        event_log.action(action)
        if isinstance(action, FeedFluxAction):
            if flux:
                await flux.send(action.audio_bytes)

        elif isinstance(action, StartAgentTurnAction):
            if agent and not (should_suppress_agent and should_suppress_agent()):
                await agent.start_turn(action.transcript, hold_check=action.hold_check)

        elif isinstance(action, ResetAgentTurnAction):
            # In IVR mode the remote party is an automated system —
            # suppress barge-in so its background audio doesn't
            # cancel the agent's response before audio is played.
            _in_ivr = ivr_mode and ivr_mode()
            if agent and not _in_ivr:
                await agent.cancel_turn()

    # ── Initialize ──────────────────────────────────────────────────

    state = AppState()
    watchdog: Optional[asyncio.Task] = None
    last_activity = [asyncio.get_event_loop().time()]
    await isp.start(on_isp_media, on_isp_start, on_isp_stop)

    # Allow LocalISP (and MockISP) to push DTMFToneEvents directly into
    # the conversation's event queue via an _inject hook.
    if hasattr(isp, '_inject'):
        isp._inject = event_queue.put_nowait

    saved: Optional[dict] = None          # Set on reconnection after take-over
    _handback_prompt: Optional[str] = None  # Set when handback has a transcript

    try:
        while True:
            # ─── RECEIVE ────────────────────────────────────────────
            event = await event_queue.get()

            event_log.event(event)

            # Update watchdog activity tracker for meaningful events
            if isinstance(event, (StreamStartEvent, FluxEndOfTurnEvent, AgentTurnDoneEvent,
                                   HoldStartEvent, HoldEndEvent)):
                last_activity[0] = asyncio.get_event_loop().time()

            # Initialize services on stream start
            if isinstance(event, StreamStartEvent):
                stream_sid = event.stream_sid

                # Check for reconnection after take-over hand-back
                saved = (await get_saved_state(event.call_sid)) if get_saved_state else None

                if saved:
                    goal = saved["goal"]
                else:
                    goal = get_goal(event.call_sid) if get_goal else os.getenv("CALL_GOAL", "")

                if flux_pool:
                    flux = await flux_pool.get(
                        on_end_of_turn=on_flux_end_of_turn,
                        on_start_of_turn=on_flux_start_of_turn,
                        on_dead=on_flux_dead,
                    )
                else:
                    flux = FluxService(
                        on_end_of_turn=on_flux_end_of_turn,
                        on_start_of_turn=on_flux_start_of_turn,
                        on_dead=on_flux_dead,
                    )
                    await flux.start()

                if _own_tts_pool:
                    await tts_pool.start()
                agent = Agent(
                    isp=isp,
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

                if saved:
                    _handback_prompt = agent.restore_history(
                        saved["history"],
                        saved["takeover_transcript"],
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
                    observer({"type": "transcript", "speaker": "callee", "text": event.transcript})
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
                await dispatch(action)

            # ─── DTMF DISPATCH ──────────────────────────────────────
            if isinstance(event, DTMFToneEvent):
                if on_dtmf:
                    on_dtmf(event.digits)  # server bookkeeping (e.g. save history for reconnect)
                await isp.send_dtmf(event.digits)  # actual DTMF action via ISP

            # ─── INITIAL GREETING / HANDBACK RESPONSE ───────────────
            # Route via process_event rather than direct state mutation so that
            # LISTENING → RESPONDING transitions are logged and auditable.
            if isinstance(event, StreamStartEvent):
                if saved:
                    # Resuming after take-over hand-back.
                    if _handback_prompt and agent:
                        state, acts = process_event(state, HandbackStartEvent(prompt=_handback_prompt))
                        for act in acts:
                            await dispatch(act)
                else:
                    # IVR mode: suppress opener — agent listens first, responds only
                    # after the IVR's EndOfTurn fires (no greeting from agent).
                    _is_ivr = ivr_mode() if ivr_mode else False
                    if not _is_ivr:
                        initial_msg = os.getenv("INITIAL_MESSAGE", "").strip()
                        opener = initial_msg or (
                            "[CALL_STARTED]" if goal else ""
                        )
                        if opener and agent:
                            state, acts = process_event(state, InitialGreetingEvent(opener=opener))
                            for act in acts:
                                await dispatch(act)

            # Start inactivity watchdog on first StreamStart
            if isinstance(event, StreamStartEvent) and watchdog is None:
                watchdog = asyncio.create_task(
                    _inactivity_watchdog(event_queue, CALL_INACTIVITY_TIMEOUT, last_activity)
                )

            # ─── EXIT CHECK ─────────────────────────────────────────
            if isinstance(event, StreamStopEvent):
                break

            if isinstance(event, HangupRequestEvent):
                if on_hangup:
                    on_hangup()  # server bookkeeping
                await isp.hangup()  # actual hangup via ISP
                if observer:
                    observer({"type": "stream_stop"})
                await isp.stop()  # stop the ISP reader
                break

    except Exception as e:
        event_log.error("Call loop", e)
        raise

    finally:
        # Cancel watchdog
        if watchdog is not None and not watchdog.done():
            watchdog.cancel()
            try:
                await watchdog
            except asyncio.CancelledError:
                pass

        await isp.stop()  # idempotent — may have been called already in hangup path

        if agent:
            await agent.cleanup()

        if _own_tts_pool:
            await tts_pool.stop()
        if flux:
            await flux.stop()  # Pool refills automatically after this

        # Save trace
        call_id = stream_sid or "unknown"
        tracer.save(call_id)

        Logger.websocket_disconnected()
