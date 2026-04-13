"""
Tests for shuo/voice_vibevoice.py.

All tests run without loading real VibeVoice model weights — the heavy
_load_model() call is patched out wherever it would touch the network or GPU.
"""

import asyncio
import base64
import math
import os
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sine_24k(duration_s: float = 0.02, freq_hz: float = 440.0) -> np.ndarray:
    """Return a float32 sine wave at 24 kHz (mono)."""
    n = int(24000 * duration_s)
    t = np.arange(n) / 24000.0
    return (np.sin(2 * math.pi * freq_hz * t)).astype(np.float32)


# ---------------------------------------------------------------------------
# _convert_chunk — pure audio-conversion tests (no model required)
# ---------------------------------------------------------------------------

class TestConvertChunk:
    def test_returns_valid_base64(self):
        from shuo.voice_vibevoice import _convert_chunk
        chunk = _sine_24k(0.02)
        b64, _ = _convert_chunk(chunk)
        # Must be decodeable base64
        decoded = base64.b64decode(b64)
        assert len(decoded) > 0

    def test_output_length_matches_8k_downsample(self):
        """480 samples @ 24 kHz → 160 samples @ 8 kHz → 160 bytes μ-law."""
        from shuo.voice_vibevoice import _convert_chunk
        chunk = _sine_24k(0.02)  # exactly 480 samples
        b64, _ = _convert_chunk(chunk)
        decoded = base64.b64decode(b64)
        # Resampled to 8kHz: 480 / 3 = 160 bytes (μ-law is 1 byte/sample)
        assert len(decoded) == 160

    def test_state_threads_through(self):
        """ratecv_state returned from one call can be passed to the next."""
        from shuo.voice_vibevoice import _convert_chunk
        chunk = _sine_24k(0.02)
        b64_1, state = _convert_chunk(chunk, None)
        # Second call with carried state should succeed
        b64_2, _ = _convert_chunk(chunk, state)
        assert b64_2 != ""

    def test_clipping_does_not_raise(self):
        """Values outside [-1, 1] are clipped, not wrapped."""
        from shuo.voice_vibevoice import _convert_chunk
        loud = np.full(480, 2.0, dtype=np.float32)  # over-driven signal
        b64, _ = _convert_chunk(loud)
        assert b64 != ""

    def test_2d_array_flattened(self):
        """2-D arrays (e.g. shape [1, N]) are accepted and flattened."""
        from shuo.voice_vibevoice import _convert_chunk
        chunk = _sine_24k(0.02).reshape(1, -1)
        b64, _ = _convert_chunk(chunk)
        assert b64 != ""


# ---------------------------------------------------------------------------
# VibeVoiceTTS lifecycle — model is mocked out
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_load_model():
    """Patch _load_model so tests never touch real weights."""
    with patch("shuo.voice_vibevoice._load_model") as m:
        yield m


@pytest.fixture()
def mock_service(mock_load_model):
    """Provide a mock StreamingTTSService singleton."""
    import shuo.voice_vibevoice as vv
    fake_service = MagicMock()
    # stream() yields one 480-sample chunk then stops
    fake_service.stream.return_value = iter([_sine_24k(0.02)])
    vv._SERVICE = fake_service
    yield fake_service
    vv._SERVICE = None


class TestVibeVoiceTTSLifecycle:
    @pytest.mark.asyncio
    async def test_start_calls_load_model(self, mock_load_model):
        from shuo.voice_vibevoice import VibeVoiceTTS
        tts = VibeVoiceTTS(AsyncMock(), AsyncMock())
        await tts.start()
        mock_load_model.assert_called_once()
        assert tts.is_active

    @pytest.mark.asyncio
    async def test_start_idempotent(self, mock_load_model):
        from shuo.voice_vibevoice import VibeVoiceTTS
        tts = VibeVoiceTTS(AsyncMock(), AsyncMock())
        await tts.start()
        await tts.start()
        mock_load_model.assert_called_once()  # still only once

    @pytest.mark.asyncio
    async def test_bind_updates_callbacks(self, mock_load_model):
        from shuo.voice_vibevoice import VibeVoiceTTS
        on_audio_1 = AsyncMock()
        on_done_1 = AsyncMock()
        on_audio_2 = AsyncMock()
        on_done_2 = AsyncMock()
        tts = VibeVoiceTTS(on_audio_1, on_done_1)
        tts.bind(on_audio_2, on_done_2)
        assert tts._on_audio is on_audio_2
        assert tts._on_done is on_done_2

    @pytest.mark.asyncio
    async def test_cancel_does_not_call_on_done(self, mock_service):
        from shuo.voice_vibevoice import VibeVoiceTTS
        on_audio = AsyncMock()
        on_done = AsyncMock()
        tts = VibeVoiceTTS(on_audio, on_done)
        await tts.start()
        await tts.cancel()
        on_done.assert_not_called()
        assert not tts.is_active

    @pytest.mark.asyncio
    async def test_send_and_flush_calls_on_done(self, mock_service):
        from shuo.voice_vibevoice import VibeVoiceTTS
        on_audio = AsyncMock()
        on_done = AsyncMock()
        tts = VibeVoiceTTS(on_audio, on_done)
        await tts.start()
        await tts.send("Hello world.")
        await tts.flush()
        on_done.assert_called_once()

    @pytest.mark.asyncio
    async def test_flush_with_no_text_still_calls_on_done(self, mock_service):
        from shuo.voice_vibevoice import VibeVoiceTTS
        on_audio = AsyncMock()
        on_done = AsyncMock()
        tts = VibeVoiceTTS(on_audio, on_done)
        await tts.start()
        await tts.flush()  # nothing was sent
        on_done.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_before_start_is_noop(self):
        from shuo.voice_vibevoice import VibeVoiceTTS
        tts = VibeVoiceTTS(AsyncMock(), AsyncMock())
        # Should not raise
        await tts.send("hello")
        assert tts._text_parts == []


# ---------------------------------------------------------------------------
# Factory integration
# ---------------------------------------------------------------------------

class TestFactory:
    def test_create_tts_vibevoice_returns_vibevoice_instance(self):
        from shuo.voice_vibevoice import VibeVoiceTTS
        with patch.dict(os.environ, {"TTS_PROVIDER": "vibevoice"}):
            from shuo.voice import _create_tts
            tts = _create_tts(on_audio=AsyncMock(), on_done=AsyncMock())
        assert isinstance(tts, VibeVoiceTTS)

    def test_create_tts_unknown_raises(self):
        with patch.dict(os.environ, {"TTS_PROVIDER": "unknown_xyz"}):
            from shuo.voice import _create_tts
            with pytest.raises(ValueError, match="Unknown TTS_PROVIDER"):
                _create_tts(on_audio=AsyncMock(), on_done=AsyncMock())
