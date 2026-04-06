"""
End-to-end test: IVR barge-in suppression.

Verifies that when ivr_mode=True, a UserSpeakingEvent while the agent is
RESPONDING does NOT cancel the agent's turn (no barge-in from IVR audio).

Without the fix, the IVR's background audio would trigger cancel_turn() before
TTS could flush, resulting in text shown on UI but no voice played.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shuo.call import (
    CallState, Phase,
    UserSpokeEvent, UserSpeakingEvent, AgentDoneEvent,
    CallStartedEvent, CallEndedEvent,
    CancelTurnAction, StartTurnAction,
    run_call,
)


# ── MockPhone ─────────────────────────────────────────────────────────────────

class MockPhone:
    """
    Fake Phone for testing.

    Fires on_start(stream_sid, call_sid, phone) during start() to simulate
    stream initialization. Accepts all Phone methods as no-ops or tracked calls.
    Supports push_stop() to trigger on_stop() and end the call loop.
    """

    def __init__(self, stream_sid="test-stream-sid", call_sid="test-call-sid", phone=""):
        self._stream_sid = stream_sid
        self._call_sid = call_sid
        self._phone = phone
        self._on_stop = None
        self.sent_audio: list[str] = []
        self.sent_dtmf: list[str] = []
        self._inject = None  # Set by call loop for LocalPhone-style DTMF injection

    async def start(self, on_media, on_start, on_stop):
        self._on_stop = on_stop
        await on_start(self._stream_sid, self._call_sid, self._phone)

    async def stop(self):
        pass

    async def send_audio(self, payload: str):
        self.sent_audio.append(payload)

    async def send_clear(self):
        pass

    async def send_dtmf(self, digit: str):
        self.sent_dtmf.append(digit)

    async def hangup(self):
        pass

    async def call(self, phone: str, twiml_url: str):
        pass

    async def push_stop(self):
        """Trigger on_stop() to push CallEndedEvent into the call loop."""
        if self._on_stop:
            await self._on_stop()


# ── Transcriber helpers ───────────────────────────────────────────────────────

class MockTranscriber:
    """
    Fake Transcriber that lets us fire turn events manually.
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

    def bind(self, on_end_of_turn, on_start_of_turn, on_interim=None, on_dead=None):
        self._on_end_of_turn = on_end_of_turn
        self._on_start_of_turn = on_start_of_turn

    async def fire_end_of_turn(self, transcript: str) -> None:
        if self._on_end_of_turn:
            await self._on_end_of_turn(transcript)

    async def fire_start_of_turn(self) -> None:
        if self._on_start_of_turn:
            await self._on_start_of_turn()


class MockTranscriberPool:
    """
    Fake TranscriberPool that returns a shared MockTranscriber.
    """

    def __init__(self, transcriber: MockTranscriber):
        self._transcriber = transcriber

    async def get(self, on_end_of_turn, on_start_of_turn, on_interim=None, on_dead=None):
        self._transcriber.bind(on_end_of_turn, on_start_of_turn, on_interim, on_dead)
        return self._transcriber

    async def stop(self) -> None:
        pass


class MockVoicePool:
    """
    Fake VoicePool that returns a mock TTS which never produces audio.
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
    In IVR mode, UserSpeakingEvent while RESPONDING must NOT cancel the
    agent's turn (barge-in is suppressed).
    """
    transcriber = MockTranscriber()
    transcriber_pool = MockTranscriberPool(transcriber)
    voice_pool = MockVoicePool()

    mock_phone = MockPhone(stream_sid="sid-1", call_sid="call-1")

    captured_agent = {}

    def on_agent_ready(agent):
        captured_agent["agent"] = agent

    # Patch Agent.cancel_turn to track calls
    cancel_turn_calls = []

    async def run_test():
        with patch("shuo.agent.Agent") as MockAgent:
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
                run_call(
                    mock_phone,
                    ivr_mode=lambda: True,
                    on_agent_ready=on_agent_ready,
                    voice_pool=voice_pool,
                    transcriber_pool=transcriber_pool,
                )
            )

            # Give the loop time to start and process the stream start
            await asyncio.sleep(0.05)

            # Simulate: IVR speaks → end of turn → agent starts responding
            await transcriber.fire_end_of_turn("Press 1 for sales, press 2 for support.")
            await asyncio.sleep(0.05)

            # Verify agent.start_turn was called (agent is now RESPONDING)
            assert agent_instance.start_turn.called, "start_turn should be called after UserSpokeEvent"

            # Simulate: IVR's background audio fires a barge-in event
            await transcriber.fire_start_of_turn()
            await asyncio.sleep(0.05)

            # Key assertion: cancel_turn must NOT have been called in IVR mode
            assert len(cancel_turn_calls) == 0, (
                f"cancel_turn was called {len(cancel_turn_calls)} time(s) "
                "but should be suppressed in IVR mode"
            )

            # Clean up
            await mock_phone.push_stop()
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
    In normal (non-IVR) mode, UserSpeakingEvent while RESPONDING MUST
    cancel the agent's turn (barge-in still active for human calls).
    """
    transcriber = MockTranscriber()
    transcriber_pool = MockTranscriberPool(transcriber)
    voice_pool = MockVoicePool()

    mock_phone = MockPhone(stream_sid="sid-2", call_sid="call-2")

    cancel_turn_calls = []

    async def run_test():
        with patch("shuo.agent.Agent") as MockAgent:
            agent_instance = MagicMock()
            agent_instance.is_turn_active = False
            agent_instance.cancel_turn = AsyncMock(side_effect=lambda: cancel_turn_calls.append("cancelled"))
            agent_instance.start_turn = AsyncMock()
            agent_instance.cleanup = AsyncMock()
            agent_instance.restore_history = MagicMock(return_value=None)
            agent_instance.history = []
            MockAgent.return_value = agent_instance

            task = asyncio.create_task(
                run_call(
                    mock_phone,
                    ivr_mode=lambda: False,   # Normal mode
                    voice_pool=voice_pool,
                    transcriber_pool=transcriber_pool,
                )
            )

            await asyncio.sleep(0.05)

            # Agent responds
            await transcriber.fire_end_of_turn("Hello there!")
            await asyncio.sleep(0.05)

            assert agent_instance.start_turn.called

            # Human barges in
            await transcriber.fire_start_of_turn()
            await asyncio.sleep(0.05)

            # In normal mode, cancel_turn SHOULD be called
            assert len(cancel_turn_calls) == 1, (
                f"Expected 1 cancel_turn call in normal mode, got {len(cancel_turn_calls)}"
            )

            await mock_phone.push_stop()
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.TimeoutError, Exception):
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    await run_test()
