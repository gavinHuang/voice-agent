# Voice Agent Platform

## What This Is

A real-time voice agent platform that enables LLM-powered agents to make and receive phone calls, navigate IVR systems, and hand off to human supervisors. Built on top of Twilio for telephony, Deepgram Flux for speech-to-text, and multiple TTS providers. The system targets ~400ms end-to-end latency from end-of-speech to first agent audio.

## Core Value

An LLM agent can call any phone number, navigate IVR menus autonomously, and be monitored/taken over by a human supervisor — all without writing telephony code.

## Requirements

### Validated

- ✓ Real-time streaming voice pipeline (Twilio → Deepgram Flux → Groq LLM → TTS → Twilio) — existing
- ✓ Barge-in: caller can interrupt agent mid-sentence — existing
- ✓ DTMF key press generation for IVR navigation — existing
- ✓ Hold detection and recovery (`[HOLD]`/`[HOLD_END]` markers) — existing
- ✓ Supervisor dashboard with live transcripts — existing
- ✓ Human takeover / handback with context injection — existing
- ✓ Multiple TTS providers (ElevenLabs, Kokoro, Fish Audio) — existing
- ✓ IVR mock server with YAML-configured flows — existing
- ✓ Browser softphone via Twilio WebRTC SDK — existing
- ✓ Pure state machine with 26 unit tests — existing
- ✓ Connection pooling for TTS and Flux — existing
- ✓ Railway deployment with graceful shutdown — existing

### Active

- [ ] ISP protocol interface — abstract Twilio behind a clean boundary
- [ ] LocalISP — in-process loopback for Twilio-free testing
- [ ] CLI tool — `voice-agent` command for all capabilities
- [ ] IVR benchmark — YAML scenarios + runner + metrics report
- [ ] Security hardening — dashboard auth, Twilio signature validation, rate limiting
- [ ] Agent framework migration — pydantic-ai replacing custom marker protocol
- [ ] Known bug fixes — DTMF lock race, TTS pool TOCTOU, trace file rotation

### Out of Scope

- Emotion detection — high complexity, low immediate value; deferred to v2
- Mobile app — web-first
- Softphone consolidation (softphone/ vs client/) — low risk to leave parallel; defer
- Building a custom ISP (non-Twilio PSTN stack) — out of scope; Twilio remains the only real ISP

## Context

The codebase (~6,740 lines Python) is production-grade and battle-tested. The core issue is not correctness but layering: `server.py` (841 lines) combines Twilio WebSocket protocol parsing, FastAPI routes, dashboard integration, and conversation lifecycle. `agent.py` (437 lines) owns LLM streaming, TTS invocation, and a custom marker-scanning protocol.

Key files to understand before modifying:
- `shuo/shuo/server.py` — entry point, Twilio WebSocket, all HTTP routes
- `shuo/shuo/conversation.py` — async event loop coordinator
- `shuo/shuo/agent.py` — LLM→TTS pipeline, marker scanning
- `shuo/shuo/types.py` — immutable state/event/action types (frozen dataclasses)
- `shuo/shuo/state.py` — 30-line pure state machine

Known bugs (from `shuo/ANALYSIS.md`):
- Race condition on `_dtmf_pending` dict (no lock)
- TTS pool TOCTOU eviction race
- Token observer callback can block LLM stream
- No call timeout — hung calls leak forever
- Trace files accumulate unbounded in `/tmp/shuo/`

## Constraints

- **Tech stack**: Python 3.9+ / FastAPI / asyncio — no framework changes except pydantic-ai addition
- **Backwards compat**: Existing Twilio integration must keep working throughout refactor
- **Test isolation**: All new unit tests must run without Twilio/Deepgram/Groq credentials
- **Latency budget**: Refactoring must not regress the ~400ms TTFT target

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| LocalISP before CLI | CLI needs local call mode to be useful; wrong order creates dead commands | — Pending |
| Protocol interfaces via Python Protocol (structural typing) | No ABCs needed; duck typing with type hints is sufficient | — Pending |
| pydantic-ai for agent framework | Mentioned in wish doc; typed tool calls replace fragile marker scanning | — Pending |
| Security before agent framework | Auth gap is a live risk; framework migration is quality-of-life | — Pending |

---
*Last updated: 2026-03-20 after initialization*
