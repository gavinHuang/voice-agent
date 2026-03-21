# Requirements: Voice Agent Platform

**Defined:** 2026-03-20
**Core Value:** An LLM agent can call any phone number, navigate IVR menus autonomously, and be monitored/taken over by a human supervisor — without writing telephony code.

## v1 Requirements

### ISP Abstraction

- [x] **ISP-01**: System defines an `ISP` protocol interface with `send_audio`, `recv_audio`, `send_dtmf`, `hangup`, `call` methods
- [x] **ISP-02**: Existing Twilio integration is refactored to implement the `ISP` protocol without changing external behavior
- [x] **ISP-03**: A `LocalISP` implementation routes audio between two in-process agents via asyncio queues (no Twilio required)
- [x] **ISP-04**: `VoiceSession` accepts any `ISP` implementation (dependency injection, not hard-coded Twilio)
- [x] **ISP-05**: All existing unit tests continue to pass after ISP abstraction

### CLI

- [x] **CLI-01**: `voice-agent serve` starts the backend server (equivalent to current `main.py`)
- [x] **CLI-02**: `voice-agent call <phone>` places an outbound call with `--goal` and `--identity` flags
- [x] **CLI-03**: `voice-agent local-call` runs a call between two agents using `LocalISP` (no Twilio)
- [x] **CLI-04**: `voice-agent bench` runs IVR benchmark scenarios from a YAML file
- [x] **CLI-05**: All CLI commands accept YAML config files; flags are overrides

### IVR Benchmark

- [x] **BENCH-01**: Benchmark scenario YAML schema defined (id, description, agent configs, success criteria, timeout)
- [ ] **BENCH-02**: Benchmark runner spawns agent ↔ IVR pairs using `LocalISP`
- [x] **BENCH-03**: Success criteria support: `transcript_contains`, `dtmf_sequence`, `max_turns`
- [ ] **BENCH-04**: Runner outputs metrics: success rate, average turns, DTMF accuracy, wall-clock latency
- [ ] **BENCH-05**: At least 3 sample scenarios provided covering the example IVR flow

### Security

- [ ] **SEC-01**: Dashboard requires authentication (token or basic auth) — unauthenticated requests get 401
- [ ] **SEC-02**: Twilio webhook requests are validated with signature verification before processing
- [ ] **SEC-03**: `/call` endpoint is rate-limited (max N calls per minute per IP)
- [ ] **SEC-04**: Trace files in `/tmp/shuo/` are rotated/cleaned (max age or max count enforced)

### Bug Fixes

- [x] **BUG-01**: `_dtmf_pending` dict access is protected by an asyncio lock
- [x] **BUG-02**: TTS pool eviction is atomic (TOCTOU race eliminated)
- [x] **BUG-03**: Token observer callback runs in a non-blocking context (does not block LLM stream)
- [x] **BUG-04**: Calls with no activity for N seconds are automatically hung up (configurable timeout)

### Agent Framework

- [ ] **AGENT-01**: `LLMAgent` is migrated to pydantic-ai with typed tool definitions
- [ ] **AGENT-02**: `[DTMF:N]`, `[HOLD]`, `[HANGUP]` markers replaced by structured `AgentResponse` type
- [ ] **AGENT-03**: Marker scanner (`MarkerScanner`) is removed after migration
- [ ] **AGENT-04**: All existing agent behaviors (DTMF, hold detection, hangup) work identically after migration
- [ ] **AGENT-05**: LLM provider (Groq/OpenAI-compatible) is configurable via pydantic-ai model selection

## v2 Requirements

### Emotion Detection

- **EMOT-01**: Voice Layer includes optional `EmotionDetector` plugin
- **EMOT-02**: Dashboard surfaces emotion events alongside transcript

### Softphone Consolidation

- **SOFT-01**: `softphone/` and `client/` merged into single implementation
- **SOFT-02**: Softphone modeled as `HumanAgent` implementing the Agent protocol

### Advanced CLI

- **CLI-06**: `voice-agent softphone` starts the browser softphone with token server
- **CLI-07**: `voice-agent dashboard` starts only the monitoring dashboard

## Out of Scope

| Feature | Reason |
|---------|--------|
| Custom PSTN stack (non-Twilio ISP) | Twilio is sufficient; building a carrier stack is a separate product |
| Mobile app | Web-first platform |
| Emotion detection (v1) | High complexity, no immediate use case |
| Multi-tenant / SaaS billing | Single-operator tool |
| OAuth for dashboard | Simple token auth is sufficient for internal tooling |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| ISP-01 | Phase 1 | Complete |
| ISP-02 | Phase 1 | Complete |
| ISP-03 | Phase 1 | Complete |
| ISP-04 | Phase 1 | Complete |
| ISP-05 | Phase 1 | Complete |
| BUG-01 | Phase 2 | Complete |
| BUG-02 | Phase 2 | Complete |
| BUG-03 | Phase 2 | Complete |
| BUG-04 | Phase 2 | Complete |
| CLI-01 | Phase 3 | Complete |
| CLI-02 | Phase 3 | Complete |
| CLI-03 | Phase 3 | Complete |
| CLI-04 | Phase 3 | Complete |
| CLI-05 | Phase 3 | Complete |
| BENCH-01 | Phase 4 | Complete |
| BENCH-02 | Phase 4 | Pending |
| BENCH-03 | Phase 4 | Complete |
| BENCH-04 | Phase 4 | Pending |
| BENCH-05 | Phase 4 | Pending |
| SEC-01 | Phase 5 | Pending |
| SEC-02 | Phase 5 | Pending |
| SEC-03 | Phase 5 | Pending |
| SEC-04 | Phase 5 | Pending |
| AGENT-01 | Phase 6 | Pending |
| AGENT-02 | Phase 6 | Pending |
| AGENT-03 | Phase 6 | Pending |
| AGENT-04 | Phase 6 | Pending |
| AGENT-05 | Phase 6 | Pending |

**Coverage:**
- v1 requirements: 27 total
- Mapped to phases: 27
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-20*
*Last updated: 2026-03-20 after initial definition*
