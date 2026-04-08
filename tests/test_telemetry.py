"""
Tests for shuo/telemetry.py — CallTelemetry and CP constants.
"""

import time
import pytest
from unittest.mock import patch

from shuo.telemetry import CallTelemetry, CP


# =============================================================================
# 7.1: Checkpoint recording, duplicate handling, counter increment, summary shape
# =============================================================================

class TestCheckpointRecording:
    def test_records_checkpoint(self):
        tel = CallTelemetry()
        tel.checkpoint(CP.CALL_CONNECTED)
        assert CP.CALL_CONNECTED in tel._checkpoints

    def test_duplicate_checkpoint_keeps_first(self):
        tel = CallTelemetry()
        tel.checkpoint(CP.LLM_START)
        first_ts = tel._checkpoints[CP.LLM_START]
        time.sleep(0.001)
        tel.checkpoint(CP.LLM_START)
        assert tel._checkpoints[CP.LLM_START] == first_ts

    def test_duplicate_checkpoint_logs_warning(self, caplog):
        import logging
        tel = CallTelemetry()
        tel.checkpoint(CP.HANGUP)
        with caplog.at_level(logging.WARNING):
            tel.checkpoint(CP.HANGUP)
        assert any("Duplicate" in r.message for r in caplog.records)

    def test_counter_increment(self):
        tel = CallTelemetry()
        tel.increment("llm_turns")
        tel.increment("llm_turns")
        tel.increment("llm_turns")
        assert tel._counters["llm_turns"] == 3

    def test_counter_increment_amount(self):
        tel = CallTelemetry()
        tel.increment("tts_segments", 5)
        assert tel._counters["tts_segments"] == 5

    def test_counter_starts_at_zero(self):
        tel = CallTelemetry()
        tel.increment("new_counter")
        assert tel._counters["new_counter"] == 1

    def test_summary_has_required_keys(self):
        tel = CallTelemetry()
        summary = tel.summary()
        assert "checkpoints" in summary
        assert "durations" in summary
        assert "counters" in summary

    def test_summary_counters_match(self):
        tel = CallTelemetry()
        tel.increment("llm_turns", 3)
        tel.increment("tts_segments", 2)
        summary = tel.summary()
        assert summary["counters"]["llm_turns"] == 3
        assert summary["counters"]["tts_segments"] == 2

    def test_all_cp_constants_are_strings(self):
        attrs = [v for k, v in vars(CP).items() if not k.startswith("_")]
        assert all(isinstance(a, str) for a in attrs)
        assert len(attrs) >= 13  # all required constants present


# =============================================================================
# 7.2: Duration calculations with synthetic timestamps
# =============================================================================

class TestDurationCalculations:
    def _tel_with_checkpoints(self, **checkpoints) -> CallTelemetry:
        """Create a CallTelemetry with synthetic monotonic timestamps."""
        tel = CallTelemetry()
        for name, offset_ms in checkpoints.items():
            tel._checkpoints[name] = offset_ms / 1000.0  # convert to seconds
        return tel

    def test_llm_ttft_ms(self):
        tel = self._tel_with_checkpoints(
            **{CP.LLM_START: 0, CP.LLM_FIRST_TOKEN: 250},
        )
        summary = tel.summary()
        assert "llm_ttft_ms" in summary["durations"]
        assert summary["durations"]["llm_ttft_ms"] == pytest.approx(250.0, abs=1.0)

    def test_connect_to_first_word_ms(self):
        tel = self._tel_with_checkpoints(
            **{CP.CALL_CONNECTED: 1000, CP.STT_FIRST_RESULT: 3500},
        )
        summary = tel.summary()
        assert "connect_to_first_word_ms" in summary["durations"]
        assert summary["durations"]["connect_to_first_word_ms"] == pytest.approx(2500.0, abs=1.0)

    def test_total_call_ms(self):
        tel = self._tel_with_checkpoints(
            **{CP.CALL_CONNECTED: 0, CP.HANGUP: 60000},
        )
        summary = tel.summary()
        assert "total_call_ms" in summary["durations"]
        assert summary["durations"]["total_call_ms"] == pytest.approx(60000.0, abs=1.0)

    def test_tts_synthesis_to_first_chunk_ms(self):
        tel = self._tel_with_checkpoints(
            **{CP.TTS_SYNTHESIS_START: 500, CP.TTS_FIRST_CHUNK: 800},
        )
        summary = tel.summary()
        assert summary["durations"]["tts_synthesis_to_first_chunk_ms"] == pytest.approx(300.0, abs=1.0)

    def test_missing_pair_omitted_from_durations(self):
        tel = CallTelemetry()
        tel.checkpoint(CP.LLM_START)
        # LLM_FIRST_TOKEN not recorded
        summary = tel.summary()
        assert "llm_ttft_ms" not in summary["durations"]

    def test_checkpoints_relative_to_call_connected(self):
        tel = self._tel_with_checkpoints(
            **{CP.CALL_CONNECTED: 1000, CP.STT_READY: 1200},
        )
        summary = tel.summary()
        # STT_READY should be 200ms after CALL_CONNECTED
        assert summary["checkpoints"]["stt_ready_ms"] == pytest.approx(200.0, abs=1.0)
        # CALL_CONNECTED itself should be 0
        assert summary["checkpoints"]["call_connected_ms"] == pytest.approx(0.0, abs=1.0)


# =============================================================================
# 7.3: Missing required checkpoint warning
# =============================================================================

class TestMissingCheckpointWarning:
    def test_warns_when_required_checkpoints_missing(self, caplog):
        import logging
        tel = CallTelemetry()
        # Record only one checkpoint — many required ones will be absent
        tel.checkpoint(CP.CALL_CONNECTED)
        with caplog.at_level(logging.WARNING):
            tel.summary()
        warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("missing" in m.lower() for m in warning_messages)

    def test_no_warning_when_all_required_present(self, caplog):
        import logging
        from shuo.telemetry import _REQUIRED_CHECKPOINTS
        tel = CallTelemetry()
        for cp in _REQUIRED_CHECKPOINTS:
            tel.checkpoint(cp)
        with caplog.at_level(logging.WARNING):
            tel.summary()
        # No warnings about missing checkpoints
        missing_warnings = [
            r for r in caplog.records
            if r.levelname == "WARNING" and "missing" in r.message.lower()
        ]
        assert missing_warnings == []

    def test_summary_omits_missing_checkpoint_keys(self):
        tel = CallTelemetry()
        # No checkpoints recorded
        summary = tel.summary()
        assert summary["checkpoints"] == {}
        assert summary["durations"] == {}
