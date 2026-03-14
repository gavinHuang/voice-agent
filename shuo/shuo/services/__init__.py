"""
External services for the shuo voice agent pipeline.

Deepgram Flux  -- STT + turn detection
OpenAI         -- LLM streaming
TTS            -- multi-provider (Kokoro/Fish/ElevenLabs) + connection pool
Twilio         -- outbound calls + audio playback
"""

from .flux import FluxService
from .llm import LLMService
from .tts import create_tts
from .tts_pool import TTSPool
from .player import AudioPlayer
from .twilio_client import make_outbound_call

__all__ = [
    "FluxService",
    "LLMService",
    "create_tts",
    "TTSPool",
    "AudioPlayer",
    "make_outbound_call",
]
