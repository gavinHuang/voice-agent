"""
End-to-end test: IVR barge-in suppression.

Verifies that when ivr_mode=True, a FluxStartOfTurnEvent while the agent is
RESPONDING does NOT cancel the agent's turn (no barge-in from IVR audio).

Without the fix, the IVR's background audio would trigger cancel_turn() before
TTS could flush, resulting in text shown on UI but no voice played.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shuo.types import (
    AppState, Phase,
    FluxEndOfTurnEvent, FluxStartOfTurnEvent, AgentTurnDoneEvent,
    StreamStartEvent, StreamStopEvent,
    ResetAgentTurnAction, StartAgentTurnAction,
)
from shuo.conversation import run_conversation_over_twilio


# ── Helpers ──────────────────────────────────────────────────────────────────

def _twilio_msg(event_type: str, **kwargs) -> str:
    """Build a JSON Twilio WebSocket message."""
    if event_type == "connected":
        return json.dumps({"event": "connected"})
    if event_type == "start":
        return json.dumps({
            "event": "start",
            "start": {
                "streamSid": kwargs.get("stream_sid", "test-stream-sid"),
                "callSid": kwargs.get("call_sid", "test-call-sid"),
                "customParameters": {},
            },
        })
    if event_type == "stop":
        return json.dumps({"event": "stop"})
    raise ValueError(f"Unknown event: {event_type}")


class MockWebSocket:
    """
    Fake Twilio WebSocket.

    Feeds a pre-baked list of messages, then blocks until the conversation
    loop closes or we push a stop event via stop().
    """

    def __init__(self, messages: list[str]):
        self._messages = list(messages)
        self._idx = 0
        self._stop_event = asyncio.Event()
        self.sent: list[str] = []

    async def receive_text(self) -> str:
        if self._idx < len(self._messages):
            msg = self._messages[self._idx]
            self._idx += 1
            return msg
        # Block until externally stopped
        await self._stop_event.wait()
        return _twilio_msg("stop")

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self._stop_event.set()

    def push_stop(self) -> None:
        self._stop_event.set()


class MockFluxService:
    """
    Fake Flux that lets us fire turn events manually.
    """

    def __init__(self):
        self._on_end_of_turn = None
        self._on_start_of_turn = None

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, audio_bytes: bytes) -> None:
        pass

    def bind(self, on_end_of_turn, on_start_of_turn, on_interim=None):
        self._on_end_of_turn = on_end_of_turn
        self._on_start_of_turn = on_start_of_turn

    async def fire_end_of_turn(self, transcript: str) -> None:
        if self._on_end_of_turn:
            await self._on_end_of_turn(transcript)

    async def fire_start_of_turn(self) -> None:
        if self._on_start_of_turn:
            await self._on_start_of_turn()


class MockFluxPool:
    """
    Fake FluxPool that returns a shared MockFluxService.
    """

    def __init__(self, flux: MockFluxService):
        self._flux = flux

    async def get(self, on_end_of_turn, on_start_of_turn, on_interim=None):
        self._flux.bind(on_end_of_turn, on_start_of_turn, on_interim)
        return self._flux

    async def stop(self) -> None:
        pass


class MockTTSPool:
    """
    Fake TTSPool that returns a mock TTS which never produces audio.
    """

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def get(self, on_audio, on_done):
        tts = AsyncMock()
        tts.bind = MagicMock()
        tts.send = AsyncMock()
        tts.flush = AsyncMock()
        tts.cancel = AsyncMock()
        return tts

    @property
    def available(self):
        return 1


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ivr_barge_in_suppressed():
    """
    In IVR mode, FluxStartOfTurnEvent while RESPONDING must NOT cancel the
    agent's turn (barge-in is suppressed).
    """
    flux = MockFluxService()
    flux_pool = MockFluxPool(flux)
    tts_pool = MockTTSPool()

    ws = MockWebSocket([
        _twilio_msg("connected"),
        _twilio_msg("start", stream_sid="sid-1", call_sid="call-1"),
    ])

    captured_agent = {}

    def on_agent_ready(agent):
        captured_agent["agent"] = agent

    # Patch Agent.cancel_turn to track calls
    cancel_turn_calls = []

    async def fake_cancel_turn(self):
        cancel_turn_calls.append("cancelled")

    async def run_test():
        with patch("shuo.conversation.Agent") as MockAgent:
            # Create a real-ish agent mock that tracks cancel_turn
            agent_instance = MagicMock()
            agent_instance.is_turn_active = False
            agent_instance.cancel_turn = AsyncMock(side_effect=lambda: cancel_turn_calls.append("cancelled"))
            agent_instance.start_turn = AsyncMock()
            agent_instance.cleanup = AsyncMock()
            agent_instance.restore_history = MagicMock(return_value=None)
            agent_instance.history = []
            MockAgent.return_value = agent_instance

            task = asyncio.create_task(
                run_conversation_over_twilio(
                    websocket=ws,
                    ivr_mode=lambda: True,
                    on_agent_ready=on_agent_ready,
                    tts_pool=tts_pool,
                    flux_pool=flux_pool,
                )
            )

            # Give the loop time to start and process the stream start
            await asyncio.sleep(0.05)

            # Simulate: IVR speaks → end of turn → agent starts responding
            await flux.fire_end_of_turn("Press 1 for sales, press 2 for support.")
            await asyncio.sleep(0.05)

            # Verify agent.start_turn was called (agent is now RESPONDING)
            assert agent_instance.start_turn.called, "start_turn should be called after FluxEndOfTurnEvent"

            # Simulate: IVR's background audio fires a barge-in event
            await flux.fire_start_of_turn()
            await asyncio.sleep(0.05)

            # Key assertion: cancel_turn must NOT have been called in IVR mode
            assert len(cancel_turn_calls) == 0, (
                f"cancel_turn was called {len(cancel_turn_calls)} time(s) "
                "but should be suppressed in IVR mode"
            )

            # Clean up
            ws.push_stop()
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.TimeoutError, Exception):
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    await run_test()


@pytest.mark.asyncio
async def test_normal_mode_barge_in_still_works():
    """
    In normal (non-IVR) mode, FluxStartOfTurnEvent while RESPONDING MUST
    cancel the agent's turn (barge-in still active for human calls).
    """
    flux = MockFluxService()
    flux_pool = MockFluxPool(flux)
    tts_pool = MockTTSPool()

    ws = MockWebSocket([
        _twilio_msg("connected"),
        _twilio_msg("start", stream_sid="sid-2", call_sid="call-2"),
    ])

    cancel_turn_calls = []

    async def run_test():
        with patch("shuo.conversation.Agent") as MockAgent:
            agent_instance = MagicMock()
            agent_instance.is_turn_active = False
            agent_instance.cancel_turn = AsyncMock(side_effect=lambda: cancel_turn_calls.append("cancelled"))
            agent_instance.start_turn = AsyncMock()
            agent_instance.cleanup = AsyncMock()
            agent_instance.restore_history = MagicMock(return_value=None)
            agent_instance.history = []
            MockAgent.return_value = agent_instance

            task = asyncio.create_task(
                run_conversation_over_twilio(
                    websocket=ws,
                    ivr_mode=lambda: False,   # Normal mode
                    tts_pool=tts_pool,
                    flux_pool=flux_pool,
                )
            )

            await asyncio.sleep(0.05)

            # Agent responds
            await flux.fire_end_of_turn("Hello there!")
            await asyncio.sleep(0.05)

            assert agent_instance.start_turn.called

            # Human barges in
            await flux.fire_start_of_turn()
            await asyncio.sleep(0.05)

            # In normal mode, cancel_turn SHOULD be called
            assert len(cancel_turn_calls) == 1, (
                f"Expected 1 cancel_turn call in normal mode, got {len(cancel_turn_calls)}"
            )

            ws.push_stop()
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.TimeoutError, Exception):
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    await run_test()
