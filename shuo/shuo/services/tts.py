"""
TTS provider abstraction.

Switch providers via TTS_PROVIDER env var:
    kokoro      — Kokoro-82M via Kokoro-FastAPI Docker (OpenAI-compatible)
    fish        — Fish Audio S2 self-hosted server
    elevenlabs  — ElevenLabs cloud API

All providers share the same interface consumed by TTSPool:
    start(), send(text), flush(), cancel(), stop(), bind()
    on_audio(base64_str) callback delivers μ-law 8 kHz chunks for Twilio.
"""

import os
from typing import Callable, Awaitable


def create_tts(
    on_audio: Callable[[str], Awaitable[None]],
    on_done: Callable[[], Awaitable[None]],
):
    """Factory — returns a TTSService matching TTS_PROVIDER env var."""
    provider = os.getenv("TTS_PROVIDER", "kokoro").lower()

    if provider == "kokoro":
        from .tts_kokoro import KokoroTTS
        return KokoroTTS(on_audio, on_done)
    elif provider == "fish":
        from .tts_fish import FishAudioTTS
        return FishAudioTTS(on_audio, on_done)
    elif provider == "elevenlabs":
        from .tts_elevenlabs import ElevenLabsTTS
        return ElevenLabsTTS(on_audio, on_done)
    else:
        raise ValueError(f"Unknown TTS_PROVIDER: {provider!r}")
