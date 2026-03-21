"""
External services for the shuo voice agent pipeline.

Deepgram Flux  -- STT + turn detection
OpenAI         -- LLM streaming
TTS            -- multi-provider (Kokoro/Fish/ElevenLabs) + connection pool
Twilio         -- outbound calls + audio playback
ISP            -- In-Session Protocol (telephony backend abstraction)
"""

from .flux import FluxService
from .llm import LLMService
from .tts import create_tts
from .tts_pool import TTSPool
from .player import AudioPlayer
from .twilio_client import make_outbound_call
from .isp import ISP
from .twilio_isp import TwilioISP
from .local_isp import LocalISP

__all__ = [
    "FluxService",
    "LLMService",
    "create_tts",
    "TTSPool",
    "AudioPlayer",
    "make_outbound_call",
    "ISP",
    "TwilioISP",
    "LocalISP",
]
