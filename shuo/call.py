"""
call.py — Everything about how a voice call works.

A call is a simple state machine driven by an event queue:

    while connected:
        event = await queue.get()                    # I/O
        state, actions = step(state, event)          # PURE
        for action in actions: await dispatch(action) # I/O

States:
    LISTENING   Waiting for the user to speak
    RESPONDING  Agent is generating and playing audio
    ENDING      Hangup requested, draining audio before disconnect

Events come from three sources:
    Phone (audio packets, stream lifecycle)
    Transcription (turn detection)
    Agent (playback complete)

All state transitions go through step() — including the initial greeting
and post-takeover handback — so they are logged, auditable, and testable.
"""

import os
import asyncio
from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import Optional, Union, List, Callable, Awaitable, Tuple

from .tracer import Tracer
from .log import Logger, get_logger

logger = get_logger("shuo.call")

CALL_INACTIVITY_TIMEOUT = float(os.getenv("CALL_INACTIVITY_TIMEOUT", "300"))


# =============================================================================
# STATE
# =============================================================================

class Phase(Enum):
    LISTENING  = auto()   # Waiting for user / user speaking
    RESPONDING = auto()   # Agent active (LLM → TTS → playback)
    ENDING     = auto()   # Hangup in progress — ignore new turns


@dataclass(frozen=True)
class CallState:
    """Routing-only state. History lives in Agent; connection metadata is local."""
    phase:     Phase = Phase.LISTENING
    hold_mode: bool  = False


# =============================================================================
# TURN OUTCOME  (produced by LanguageModel, consumed by Agent)
# =============================================================================

@dataclass(frozen=True)
class TurnOutcome:
    """
    What happened in this LLM turn — resolved by LanguageModel.resolve_outcome().

    Priority for dispatch:
      hold_continue  → silent done (on-hold wait, skip TTS)
      dtmf_digits    → send digit, suppress speech
      has_speech     → flush TTS and play
      (else)         → empty turn, silent done
    """
    dtmf_digits:    Optional[str] = None
    hold_continue:  bool = False
    emit_hold_start: bool = False
    emit_hold_end:  bool = False
    hangup:         bool = False
    has_speech:     bool = False


# =============================================================================
# EVENTS  (inputs — what can happen during a call)
# =============================================================================

@dataclass(frozen=True)
class CallStartedEvent:
    """Phone stream connected."""
    stream_sid: str
    call_sid:   str = ""
    phone:      str = ""


@dataclass(frozen=True)
class CallEndedEvent:
    """Phone stream disconnected."""


@dataclass(frozen=True)
class AudioChunkEvent:
    """Raw audio from the caller."""
    audio_bytes: bytes


@dataclass(frozen=True)
class UserSpeakingEvent:
    """Transcription service detected the user started speaking (barge-in trigger)."""


@dataclass(frozen=True)
class UserSpokeEvent:
    """Transcription service detected the user finished their turn."""
    transcript: str


@dataclass(frozen=True)
class AgentDoneEvent:
    """Agent finished speaking (playback complete)."""


@dataclass(frozen=True)
class HoldStartEvent:
    """Agent detected it is on hold — suppress barge-in."""


@dataclass(frozen=True)
class HoldEndEvent:
    """Agent detected a real person returned — exit hold mode."""


@dataclass(frozen=True)
class HangupPendingEvent:
    """Agent detected [HANGUP] — block new turns while goodbye plays."""


@dataclass(frozen=True)
class HangupEvent:
    """Agent finished goodbye turn — time to disconnect."""


@dataclass(frozen=True)
class DTMFEvent:
    """Agent wants to send DTMF digits via the phone provider."""
    digits: str


@dataclass(frozen=True)
class GreetEvent:
    """
    Synthetic: triggers the opening agent turn when a call connects.
    Routes through step() so the transition is logged like any other.
    """
    opener: str


@dataclass(frozen=True)
class HandbackEvent:
    """
    Synthetic: resumes the agent after a human supervisor take-over.
    Routes through step() so the transition is logged.
    """
    prompt: str


Event = Union[
    CallStartedEvent, CallEndedEvent, AudioChunkEvent,
    UserSpeakingEvent, UserSpokeEvent,
    AgentDoneEvent,
    HoldStartEvent, HoldEndEvent,
    HangupPendingEvent, HangupEvent,
    DTMFEvent,
    GreetEvent, HandbackEvent,
]


# =============================================================================
# ACTIONS  (outputs — what to do in response to events)
# =============================================================================

@dataclass(frozen=True)
class StreamToSTTAction:
    """Forward audio bytes to the transcription service."""
    audio_bytes: bytes


@dataclass(frozen=True)
class StartTurnAction:
    """Start an agent response turn."""
    transcript: str
    hold_check: bool = False


@dataclass(frozen=True)
class CancelTurnAction:
    """Interrupt the current agent turn (barge-in)."""


Action = Union[StreamToSTTAction, StartTurnAction, CancelTurnAction]


# =============================================================================
# STATE MACHINE  (pure)
# =============================================================================

def step(state: CallState, event: Event) -> Tuple[CallState, List[Action]]:
    """
    Pure state machine: (CallState, Event) → (CallState, List[Action])

    Every state transition in the system goes through here — no exceptions.
    """
    if state.phase == Phase.ENDING:
        return state, []

    if isinstance(event, CallStartedEvent):
        return replace(state, phase=Phase.LISTENING), []

    if isinstance(event, CallEndedEvent):
        actions: List[Action] = []
        if state.phase == Phase.RESPONDING:
            actions.append(CancelTurnAction())
        return state, actions

    if isinstance(event, AudioChunkEvent):
        return state, [StreamToSTTAction(audio_bytes=event.audio_bytes)]

    if isinstance(event, UserSpokeEvent):
        if event.transcript and state.phase == Phase.LISTENING:
            return replace(state, phase=Phase.RESPONDING), [
                StartTurnAction(transcript=event.transcript, hold_check=state.hold_mode)
            ]
        return state, []

    if isinstance(event, UserSpeakingEvent):
        if state.phase == Phase.RESPONDING and not state.hold_mode:
            return replace(state, phase=Phase.LISTENING), [CancelTurnAction()]
        return state, []

    if isinstance(event, AgentDoneEvent):
        if state.phase == Phase.RESPONDING:
            return replace(state, phase=Phase.LISTENING), []
        return state, []

    if isinstance(event, HoldStartEvent):
        return replace(state, hold_mode=True), []

    if isinstance(event, HoldEndEvent):
        return replace(state, hold_mode=False), []

    if isinstance(event, (HangupPendingEvent, HangupEvent)):
        return replace(state, phase=Phase.ENDING), []

    if isinstance(event, GreetEvent):
        if state.phase == Phase.LISTENING:
            return replace(state, phase=Phase.RESPONDING), [
                StartTurnAction(transcript=event.opener)
            ]
        return state, []

    if isinstance(event, HandbackEvent):
        if state.phase == Phase.LISTENING:
            return replace(state, phase=Phase.RESPONDING), [
                StartTurnAction(transcript=event.prompt)
            ]
        return state, []

    return state, []


# =============================================================================
# INACTIVITY WATCHDOG
# =============================================================================

async def _inactivity_watchdog(
    queue: asyncio.Queue,
    timeout: float,
    last_activity: list,   # [float] — shared mutable timestamp
) -> None:
    """Hang up if no meaningful call activity for `timeout` seconds."""
    try:
        while True:
            await asyncio.sleep(min(5.0, timeout))
            now = asyncio.get_event_loop().time()
            if now - last_activity[0] > timeout:
                logger.warning(f"Inactivity timeout ({timeout}s) — requesting hangup")
                await queue.put(HangupEvent())
                return
    except asyncio.CancelledError:
        pass


# =============================================================================
# CALL LOOP
# =============================================================================

async def run_call(
    phone,
    observer:              Optional[Callable[[dict], None]]                      = None,
    should_suppress_agent: Optional[Callable[[], bool]]                          = None,
    on_agent_ready:        Optional[Callable[["Agent"], None]]                   = None,
    get_goal:              Optional[Callable[[str], str]]                        = None,
    get_saved_state:       Optional[Callable[[str], "Awaitable[Optional[dict]]"]] = None,
    voice_pool:            Optional["VoicePool"]                                 = None,
    transcriber_pool:      Optional["TranscriberPool"]                           = None,
    ivr_mode:              Optional[Callable[[], bool]]                          = None,
    on_dtmf:               Optional[Callable[[str], None]]                       = None,
    ctx:                   Optional[object]                                      = None,
) -> None:
    """
    Drive a single call from connect to disconnect.

    Wires callbacks → event queue → step() → dispatch().
    All side effects happen in dispatch(); step() is kept pure.
    """
    from .agent import Agent
    from .speech import Transcriber, TranscriberPool
    from .voice import VoicePool

    event_log = Logger(verbose=False)
    queue: asyncio.Queue[Event] = asyncio.Queue()
    tracer = Tracer()

    agent: Optional[Agent] = None
    transcriber = None
    _own_voice_pool = voice_pool is None
    if _own_voice_pool:
        voice_pool = VoicePool(pool_size=1, ttl=8.0)
    stream_sid: Optional[str] = None

    # ── Transcription callbacks ──────────────────────────────────────

    async def on_transcript(transcript: str) -> None:
        await queue.put(UserSpokeEvent(transcript=transcript))

    async def on_speech_started() -> None:
        await queue.put(UserSpeakingEvent())

    async def on_transcriber_dead() -> None:
        logger.error("STT permanently unavailable — hanging up")
        await queue.put(HangupEvent())

    # ── Phone callbacks ──────────────────────────────────────────────

    async def on_audio(audio_bytes: bytes) -> None:
        await queue.put(AudioChunkEvent(audio_bytes=audio_bytes))

    async def on_call_started(stream_sid_: str, call_sid: str, phone_number: str) -> None:
        await queue.put(CallStartedEvent(
            stream_sid=stream_sid_,
            call_sid=call_sid,
            phone=phone_number,
        ))

    async def on_call_ended() -> None:
        await queue.put(CallEndedEvent())

    # ── Action dispatcher ────────────────────────────────────────────

    async def dispatch(action: Action) -> None:
        event_log.action(action)

        if isinstance(action, StreamToSTTAction):
            if transcriber:
                await transcriber.send(action.audio_bytes)

        elif isinstance(action, StartTurnAction):
            if agent and not (should_suppress_agent and should_suppress_agent()):
                await agent.start_turn(action.transcript, hold_check=action.hold_check)

        elif isinstance(action, CancelTurnAction):
            # In IVR mode the remote party is automated — suppress barge-in so
            # background audio doesn't cancel the agent's response mid-flight.
            # Also suppress if hangup is already decided — let goodbye play out.
            if agent and not (ivr_mode and ivr_mode()) and not agent.hangup_decided:
                await agent.cancel_turn()

    # ── Boot ────────────────────────────────────────────────────────

    state = CallState()
    watchdog: Optional[asyncio.Task] = None
    last_activity = [asyncio.get_event_loop().time()]
    await phone.start(on_audio, on_call_started, on_call_ended)

    if hasattr(phone, '_inject'):
        phone._inject = queue.put_nowait

    saved: Optional[dict] = None
    _handback_prompt: Optional[str] = None

    try:
        while True:
            # ── RECEIVE ─────────────────────────────────────────────
            event = await queue.get()
            event_log.event(event)

            # Activity ping for inactivity watchdog
            if isinstance(event, (CallStartedEvent, UserSpokeEvent, AgentDoneEvent,
                                   HoldStartEvent, HoldEndEvent)):
                last_activity[0] = asyncio.get_event_loop().time()

            # ── INIT on call start ───────────────────────────────────
            if isinstance(event, CallStartedEvent):
                stream_sid = event.stream_sid
                saved = (await get_saved_state(event.call_sid)) if get_saved_state else None

                goal = (
                    saved["goal"] if saved
                    else (get_goal(event.call_sid) if get_goal
                          else (ctx.goal if ctx else os.getenv("CALL_GOAL", "")))
                )

                if transcriber_pool:
                    transcriber = await transcriber_pool.get(
                        on_end_of_turn=on_transcript,
                        on_start_of_turn=on_speech_started,
                        on_dead=on_transcriber_dead,
                    )
                else:
                    from .speech import Transcriber
                    transcriber = Transcriber(
                        on_end_of_turn=on_transcript,
                        on_start_of_turn=on_speech_started,
                        on_dead=on_transcriber_dead,
                    )
                    logger.info("[BP4] Starting transcriber...")
                    await transcriber.start()
                    logger.info("[BP4] Transcriber started OK")

                if _own_voice_pool:
                    logger.info("[BP4] Starting voice pool...")
                    await voice_pool.start()
                    logger.info("[BP4] Voice pool started OK")

                agent = Agent(
                    phone=phone,
                    stream_sid=event.stream_sid,
                    emit=lambda e: queue.put_nowait(e),
                    voice_pool=voice_pool,
                    tracer=tracer,
                    goal=goal,
                    ctx=ctx if not saved else None,
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

            # ── STEP (pure) ──────────────────────────────────────────
            old_phase = state.phase
            state, actions = step(state, event)
            event_log.transition(old_phase, state.phase)

            # ── OBSERVE ─────────────────────────────────────────────
            if observer:
                if isinstance(event, CallStartedEvent):
                    observer({
                        "type":       "stream_start",
                        "call_sid":   event.call_sid,
                        "stream_sid": event.stream_sid,
                        "phone":      event.phone,
                    })
                elif isinstance(event, UserSpokeEvent) and event.transcript:
                    observer({"type": "transcript", "speaker": "callee", "text": event.transcript})
                elif isinstance(event, AgentDoneEvent):
                    observer({"type": "agent_done"})
                elif isinstance(event, HoldStartEvent):
                    observer({"type": "hold"})
                elif isinstance(event, HoldEndEvent):
                    observer({"type": "hold_end"})
                elif isinstance(event, CallEndedEvent):
                    observer({"type": "stream_stop"})
                if old_phase != state.phase:
                    observer({"type": "phase_change", "from": old_phase.name, "to": state.phase.name})

            # ── DISPATCH ─────────────────────────────────────────────
            for action in actions:
                await dispatch(action)

            # ── DTMF ─────────────────────────────────────────────────
            if isinstance(event, DTMFEvent):
                if on_dtmf:
                    await on_dtmf(event.digits)
                await phone.send_dtmf(event.digits)

            # ── GREETING / HANDBACK ──────────────────────────────────
            if isinstance(event, CallStartedEvent):
                if saved:
                    if _handback_prompt and agent:
                        state, acts = step(state, HandbackEvent(prompt=_handback_prompt))
                        for act in acts:
                            await dispatch(act)
                else:
                    _is_ivr = ivr_mode() if ivr_mode else False
                    if not _is_ivr:
                        initial_msg = os.getenv("INITIAL_MESSAGE", "").strip()
                        opener = initial_msg or ("[CALL_STARTED]" if goal else "")
                        if opener and agent:
                            state, acts = step(state, GreetEvent(opener=opener))
                            for act in acts:
                                await dispatch(act)

            # ── WATCHDOG START ───────────────────────────────────────
            if isinstance(event, CallStartedEvent) and watchdog is None:
                watchdog = asyncio.create_task(
                    _inactivity_watchdog(queue, CALL_INACTIVITY_TIMEOUT, last_activity)
                )

            # ── EXIT ─────────────────────────────────────────────────
            if isinstance(event, CallEndedEvent):
                break

            if isinstance(event, HangupEvent):
                await phone.hangup()
                if observer:
                    observer({"type": "stream_stop"})
                await phone.stop()
                break

    except Exception as e:
        event_log.error("Call loop", e)
        raise

    finally:
        if watchdog and not watchdog.done():
            watchdog.cancel()
            try:
                await watchdog
            except asyncio.CancelledError:
                pass

        await phone.stop()

        if agent:
            await agent.cleanup()

        if _own_voice_pool:
            await voice_pool.stop()

        if transcriber:
            await transcriber.stop()

        tracer.save(stream_sid or "unknown")
        Logger.websocket_disconnected()
