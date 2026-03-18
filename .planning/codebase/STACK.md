# Technology Stack

**Analysis Date:** 2026-03-18

## Languages

**Primary:**
- Python 3.9+ - Core voice agent framework and backend services in `shuo/`, `dashboard/`, `ivr/`, and utilities

## Runtime

**Environment:**
- Python 3.9+ (tested with 3.12, 3.13, 3.14)

**Package Manager:**
- pip
- Lockfile: `shuo/requirements.txt` (manually maintained)

## Frameworks

**Core:**
- FastAPI 0.109.0+ - Web framework for voice agent server in `shuo/server.py`, dashboard API in `dashboard/server.py`, and IVR server in `ivr/server.py`
- Uvicorn 0.27.0+ - ASGI server for FastAPI applications

**Real-time Communication:**
- Twilio SDK 9.0.0+ - Voice API for outbound/inbound calls and WebSocket media streams
  - WebSocket streaming for μ-law 8kHz audio
  - REST API for call control, DTMF, recordings
  - JWT access tokens for browser softphone

**Speech-to-Text:**
- Deepgram SDK 3.0.0+ - Flux API for always-on streaming STT with turn detection
  - Replaces separate VAD and STT services
  - Continuously processes Twilio audio
  - Emits EndOfTurn and StartOfTurn events

**LLM:**
- OpenAI SDK 1.0.0+ - Client for Groq API (OpenAI-compatible)
  - Used for streaming LLM responses in `shuo/services/llm.py`
  - Groq model: `llama-3.3-70b-versatile` (default, configurable)

**Text-to-Speech:**
- ElevenLabs WebSocket streaming - Cloud TTS provider
  - Custom WebSocket client via `websockets` 12.0+
  - Default voice: Rachel (ID: `21m00Tcm4TlvDq8ikWAM`)
  - Model: `eleven_flash_v2_5` (default)
- Kokoro-82M - Local TTS via Docker (zero API cost alternative)
- Fish Audio S2 - Self-hosted TTS option

**Testing:**
- pytest 7.0.0+ - Test framework
- pytest-asyncio 0.21.0+ - Async test support

**Build/Dev:**
- python-dotenv 1.0.0+ - Environment variable loading from `.env`
- NumPy 1.24.0+ - Audio processing support
- Matplotlib 3.7.0+ - Visualization (docs/charting)
- httpx 0.27.0+ - HTTP client (async)
- audioop-lts 0.2.1+ - Audio operation library for Python 3.13+ compatibility

## Key Dependencies

**Critical:**
- `twilio` - Voice call signaling and WebSocket media stream handling
- `deepgram-sdk` - Speech-to-text and turn detection
- `openai` - LLM client (Groq-compatible)
- `websockets` - WebSocket protocol for ElevenLabs TTS and Deepgram Flux

**Audio:**
- `numpy` - Audio array operations
- `audioop-lts` - Audio codec operations (fallback for Python 3.13+)

**Infrastructure:**
- `fastapi` - HTTP server framework
- `uvicorn` - ASGI application server
- `python-dotenv` - Configuration via environment variables

## Configuration

**Environment:**
- Configuration via `.env` file in `shuo/` directory
- `.env.example` provides template with all required variables
- REQUIRED variables checked in `shuo/main.py:check_environment()`

**Build:**
- No build step required — pure Python
- `shuo/Procfile` for Railway deployment: `web: python main.py`

## Platform Requirements

**Development:**
- Python 3.9+
- ngrok (for local development — Twilio requires public HTTPS URL)
- Second tunnel tool for IVR testing (e.g., `localhost.run`)

**Production:**
- Railway (primary deployment target)
- Or any Python 3.9+ WSGI/ASGI-capable host
- Public HTTPS URL (set as `TWILIO_PUBLIC_URL`)
- Environment variables for all API keys

**Audio Processing:**
- μ-law 8kHz codec (Twilio native)
- WebSocket streaming for real-time audio
- Optional: Docker for Kokoro local TTS (`tts_kokoro.py`)

---

*Stack analysis: 2026-03-18*
