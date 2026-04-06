"""
Unit tests for the pure step() function.

These tests verify the state machine logic without any I/O.
With Deepgram Flux handling turn detection, the state machine
is a simple conversation controller.
"""

import pytest
from dataclasses import replace

from shuo.call import (
    CallState, Phase,
    CallStartedEvent, CallEndedEvent, AudioChunkEvent,
    UserSpeakingEvent, UserSpokeEvent, AgentDoneEvent,
    StreamToSTTAction, StartTurnAction, CancelTurnAction,
    step,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def initial_state() -> CallState:
    """Fresh state at the start of a call."""
    return CallState()


@pytest.fixture
def listening_state() -> CallState:
    """State after stream has started."""
    return CallState(phase=Phase.LISTENING)


@pytest.fixture
def responding_state() -> CallState:
    """State while agent is responding."""
    return CallState(phase=Phase.RESPONDING)


# =============================================================================
# STREAM LIFECYCLE
# =============================================================================

class TestStreamLifecycle:

    def test_stream_start_transitions_to_listening(self, initial_state):
        """CallStartedEvent should transition to LISTENING with no actions."""
        event = CallStartedEvent(stream_sid="new-stream-123")
        new_state, actions = step(initial_state, event)

        assert new_state.phase == Phase.LISTENING
        assert actions == []

    def test_stream_start_resets_phase(self):
        """CallStartedEvent should reset to LISTENING even if RESPONDING."""
        state = CallState(phase=Phase.RESPONDING)
        event = CallStartedEvent(stream_sid="new")
        new_state, _ = step(state, event)

        assert new_state.phase == Phase.LISTENING

    def test_stream_stop_resets_agent_if_responding(self, responding_state):
        """CallEndedEvent should reset agent turn if responding."""
        event = CallEndedEvent()
        _, actions = step(responding_state, event)

        assert len(actions) == 1
        assert isinstance(actions[0], CancelTurnAction)

    def test_stream_stop_no_action_if_listening(self, listening_state):
        """CallEndedEvent should produce no actions when listening."""
        event = CallEndedEvent()
        _, actions = step(listening_state, event)

        assert actions == []


# =============================================================================
# MEDIA ROUTING
# =============================================================================

class TestMediaRouting:

    def test_media_feeds_stt(self, listening_state):
        """AudioChunkEvent should always produce StreamToSTTAction."""
        event = AudioChunkEvent(audio_bytes=b"\x00\x01\x02")
        _, actions = step(listening_state, event)

        assert len(actions) == 1
        assert isinstance(actions[0], StreamToSTTAction)
        assert actions[0].audio_bytes == b"\x00\x01\x02"

    def test_media_feeds_stt_in_any_phase(self, responding_state):
        """Audio should flow to STT regardless of phase."""
        event = AudioChunkEvent(audio_bytes=b"\xff")
        _, actions = step(responding_state, event)

        assert len(actions) == 1
        assert isinstance(actions[0], StreamToSTTAction)

    def test_media_does_not_change_state(self, listening_state):
        """AudioChunkEvent should not change application state."""
        event = AudioChunkEvent(audio_bytes=b"\x00")
        new_state, _ = step(listening_state, event)

        assert new_state == listening_state


# =============================================================================
# TURN EVENTS
# =============================================================================

class TestUserSpokeEvent:

    def test_end_of_turn_starts_agent(self, listening_state):
        """UserSpokeEvent with transcript should start agent turn."""
        event = UserSpokeEvent(transcript="Hello, how are you?")
        new_state, actions = step(listening_state, event)

        assert new_state.phase == Phase.RESPONDING
        assert len(actions) == 1
        assert isinstance(actions[0], StartTurnAction)
        assert actions[0].transcript == "Hello, how are you?"

    def test_end_of_turn_empty_transcript_ignored(self, listening_state):
        """Empty transcript should be ignored."""
        event = UserSpokeEvent(transcript="")
        new_state, actions = step(listening_state, event)

        assert new_state.phase == Phase.LISTENING
        assert actions == []

    def test_end_of_turn_ignored_if_responding(self, responding_state):
        """UserSpokeEvent should be ignored if already responding."""
        event = UserSpokeEvent(transcript="Interrupt text")
        new_state, actions = step(responding_state, event)

        assert new_state.phase == Phase.RESPONDING  # Unchanged
        assert actions == []


class TestUserSpeakingEvent:

    def test_start_of_turn_interrupts_agent(self, responding_state):
        """UserSpeakingEvent during RESPONDING should trigger barge-in."""
        event = UserSpeakingEvent()
        new_state, actions = step(responding_state, event)

        assert new_state.phase == Phase.LISTENING
        assert len(actions) == 1
        assert isinstance(actions[0], CancelTurnAction)

    def test_start_of_turn_ignored_if_listening(self, listening_state):
        """UserSpeakingEvent during LISTENING should be a no-op."""
        event = UserSpeakingEvent()
        new_state, actions = step(listening_state, event)

        assert new_state.phase == Phase.LISTENING
        assert actions == []


# =============================================================================
# AGENT TURN DONE
# =============================================================================

class TestAgentDone:

    def test_done_transitions_to_listening(self, responding_state):
        """AgentDoneEvent should transition back to LISTENING."""
        event = AgentDoneEvent()
        new_state, actions = step(responding_state, event)

        assert new_state.phase == Phase.LISTENING
        assert actions == []

    def test_done_ignored_if_listening(self, listening_state):
        """AgentDoneEvent should be ignored if already listening."""
        event = AgentDoneEvent()
        new_state, actions = step(listening_state, event)

        assert new_state.phase == Phase.LISTENING
        assert actions == []


# =============================================================================
# COMPLETE FLOW
# =============================================================================

class TestCompleteFlow:

    def test_full_conversation_turn(self, listening_state):
        """Complete turn: UserSpokeEvent -> Agent responds -> Done."""
        state = listening_state

        # User finishes speaking
        state, actions = step(state, UserSpokeEvent(transcript="Hello"))
        assert state.phase == Phase.RESPONDING
        assert any(isinstance(a, StartTurnAction) for a in actions)

        # Agent finishes speaking
        state, actions = step(state, AgentDoneEvent())
        assert state.phase == Phase.LISTENING
        assert actions == []

    def test_interrupt_during_response(self, listening_state):
        """Barge-in: Agent responding -> UserSpeakingEvent -> Reset."""
        state = listening_state

        # Start responding
        state, _ = step(state, UserSpokeEvent(transcript="Hello"))
        assert state.phase == Phase.RESPONDING

        # User interrupts
        state, actions = step(state, UserSpeakingEvent())
        assert state.phase == Phase.LISTENING
        assert any(isinstance(a, CancelTurnAction) for a in actions)

    def test_multi_turn(self, listening_state):
        """Multiple turns work correctly."""
        state = listening_state

        # Turn 1
        state, _ = step(state, UserSpokeEvent(transcript="Hi"))
        assert state.phase == Phase.RESPONDING
        state, _ = step(state, AgentDoneEvent())
        assert state.phase == Phase.LISTENING

        # Turn 2
        state, _ = step(state, UserSpokeEvent(transcript="How are you?"))
        assert state.phase == Phase.RESPONDING
        state, _ = step(state, AgentDoneEvent())
        assert state.phase == Phase.LISTENING

    def test_audio_always_forwarded_to_stt(self, listening_state):
        """AudioChunkEvents should always produce StreamToSTTAction."""
        state = listening_state

        # While listening
        _, actions = step(state, AudioChunkEvent(audio_bytes=b"\x00"))
        assert isinstance(actions[0], StreamToSTTAction)

        # While responding
        state = replace(state, phase=Phase.RESPONDING)
        _, actions = step(state, AudioChunkEvent(audio_bytes=b"\x00"))
        assert isinstance(actions[0], StreamToSTTAction)

    def test_interrupt_then_new_turn(self, listening_state):
        """After barge-in, a new turn can start."""
        state = listening_state

        # Start turn
        state, _ = step(state, UserSpokeEvent(transcript="Hello"))
        assert state.phase == Phase.RESPONDING

        # Interrupt
        state, actions = step(state, UserSpeakingEvent())
        assert state.phase == Phase.LISTENING
        assert any(isinstance(a, CancelTurnAction) for a in actions)

        # New turn after interrupt
        state, actions = step(state, UserSpokeEvent(transcript="Never mind, goodbye"))
        assert state.phase == Phase.RESPONDING
        assert any(isinstance(a, StartTurnAction) for a in actions)
        assert actions[0].transcript == "Never mind, goodbye"


# =============================================================================
# EDGE CASES
# =============================================================================

class TestEdgeCases:

    def test_state_immutability(self, initial_state):
        """State updates should not mutate original."""
        event = CallStartedEvent(stream_sid="new-sid")
        new_state, _ = step(initial_state, event)

        # CallState is immutable; original must be unchanged
        assert initial_state.phase == Phase.LISTENING
        assert new_state is not initial_state

    def test_stream_stop_in_listening_is_safe(self, listening_state):
        """CallEndedEvent while listening should produce no actions."""
        _, actions = step(listening_state, CallEndedEvent())
        assert actions == []

    def test_agent_done_in_wrong_phase_is_safe(self, listening_state):
        """AgentDoneEvent in LISTENING should not crash."""
        new_state, actions = step(listening_state, AgentDoneEvent())
        assert new_state.phase == Phase.LISTENING
        assert actions == []

    def test_start_of_turn_in_listening_is_safe(self, listening_state):
        """UserSpeakingEvent in LISTENING is normal (user talking)."""
        new_state, actions = step(listening_state, UserSpeakingEvent())
        assert new_state.phase == Phase.LISTENING
        assert actions == []

    def test_end_of_turn_in_listening_starts_agent(self, initial_state):
        """UserSpokeEvent with transcript in LISTENING should start agent."""
        new_state, actions = step(initial_state, UserSpokeEvent(transcript="test"))
        assert new_state.phase == Phase.RESPONDING
        assert len(actions) == 1
