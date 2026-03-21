# Roadmap: Voice Agent Platform

## Overview

Six phases of targeted work on a production-grade codebase. Phase 1 builds the ISP abstraction that makes in-process testing possible. Phase 2 fixes the known race conditions before anything else is built on top of them. Phase 3 delivers the CLI, which depends on LocalISP for the `local-call` command. Phase 4 builds the IVR benchmark suite on top of LocalISP and the CLI. Phase 5 hardens security while the codebase is otherwise stable. Phase 6 migrates the agent to pydantic-ai, replacing the custom marker protocol with structured output.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: ISP Abstraction** - Define ISP protocol, implement LocalISP, inject into VoiceSession (completed 2026-03-21)
- [x] **Phase 2: Bug Fixes** - Eliminate race conditions and plug hung-call / disk-leak issues (completed 2026-03-21)
- [x] **Phase 3: CLI** - `voice-agent` command covering serve, call, local-call, bench (completed 2026-03-21)
- [ ] **Phase 4: IVR Benchmark** - YAML scenario runner with metrics using LocalISP
- [ ] **Phase 5: Security Hardening** - Dashboard auth, Twilio signature validation, rate limiting, trace rotation
- [ ] **Phase 6: Agent Framework Migration** - pydantic-ai replaces custom marker protocol

## Phase Details

### Phase 1: ISP Abstraction
**Goal**: VoiceSession is decoupled from Twilio — any ISP implementation can be injected, and calls can run entirely in-process via LocalISP
**Depends on**: Nothing (first phase)
**Requirements**: ISP-01, ISP-02, ISP-03, ISP-04, ISP-05
**Success Criteria** (what must be TRUE):
  1. A `LocalISP` instance can connect two in-process agents — audio written by one is readable by the other — without any Twilio credentials or network calls
  2. The existing Twilio integration continues to handle real calls identically to before the refactor
  3. All 26 existing unit tests pass against the refactored code
  4. `VoiceSession` can be constructed with either `TwilioISP` or `LocalISP` by passing the instance at construction time
**Plans:** 3/3 plans complete
Plans:
- [ ] 01-01-PLAN.md — ISP Protocol definition + LocalISP implementation with tests
- [ ] 01-02-PLAN.md — TwilioISP implementation + refactor AudioPlayer and Agent to use ISP
- [ ] 01-03-PLAN.md — Wire ISP into conversation.py and server.py + update integration tests

### Phase 2: Bug Fixes
**Goal**: The four known correctness issues are eliminated so subsequent phases build on a stable foundation
**Depends on**: Phase 1
**Requirements**: BUG-01, BUG-02, BUG-03, BUG-04
**Success Criteria** (what must be TRUE):
  1. Concurrent DTMF events on the same call cannot corrupt `_dtmf_pending` — verified by a stress test or lock audit
  2. The TTS pool never dispenses an item that has already been evicted — the eviction path is atomic
  3. A slow token observer callback does not stall the LLM stream — the observer runs in a non-blocking context
  4. A call with no activity for a configurable number of seconds is automatically hung up, freeing its resources
**Plans:** 2/2 plans complete
Plans:
- [ ] 02-01-PLAN.md — Test scaffold + BUG-01 (_dtmf_pending lock) + BUG-02 (TTS pool lock)
- [ ] 02-02-PLAN.md — BUG-03 (non-blocking observer) + BUG-04 (inactivity watchdog)

### Phase 3: CLI
**Goal**: A `voice-agent` command provides a single entry point for all platform capabilities, with YAML config files and flag overrides
**Depends on**: Phase 1, Phase 2
**Requirements**: CLI-01, CLI-02, CLI-03, CLI-04, CLI-05
**Success Criteria** (what must be TRUE):
  1. `voice-agent serve` starts the backend server; the running server is identical in behavior to the current `main.py` entry point
  2. `voice-agent call <phone> --goal "..." --identity "..."` initiates an outbound Twilio call
  3. `voice-agent local-call` runs a full agent-to-agent conversation in-process using LocalISP — no Twilio credentials needed
  4. `voice-agent bench` accepts a YAML scenario file and runs benchmark scenarios
  5. Every command accepts a YAML config file; CLI flags are treated as overrides of config file values
**Plans:** 2/2 plans complete
Plans:
- [ ] 03-01-PLAN.md — pyproject.toml + Click CLI with serve, call, bench subcommands + config loading + tests
- [ ] 03-02-PLAN.md — local-call subcommand with concurrent LocalISP conversations + live transcript + tests

### Phase 4: IVR Benchmark
**Goal**: A repeatable benchmark suite can evaluate how reliably the LLM agent navigates IVR systems, with structured metrics output
**Depends on**: Phase 1, Phase 3
**Requirements**: BENCH-01, BENCH-02, BENCH-03, BENCH-04, BENCH-05
**Success Criteria** (what must be TRUE):
  1. A YAML scenario file with `transcript_contains`, `dtmf_sequence`, and `max_turns` criteria can be authored without writing Python code
  2. `voice-agent bench --dataset scenarios.yaml` runs all scenarios, each spawning a LocalISP-connected agent + IVR pair
  3. The runner prints a metrics report with success rate, average turns, DTMF accuracy, and wall-clock latency per scenario
  4. At least 3 sample scenarios covering the example IVR flow are included and pass against the existing mock IVR server
**Plans:** 2/3 plans executed
Plans:
- [ ] 04-01-PLAN.md — YAML scenario schema, data model, success criteria evaluation + test scaffold
- [ ] 04-02-PLAN.md — IVR driver, benchmark runner, metrics report, CLI wiring
- [ ] 04-03-PLAN.md — Sample scenario e2e validation against IVR mock

### Phase 5: Security Hardening
**Goal**: The dashboard and call endpoints are protected against unauthorized access, spoofed webhooks, and resource abuse
**Depends on**: Phase 2
**Requirements**: SEC-01, SEC-02, SEC-03, SEC-04
**Success Criteria** (what must be TRUE):
  1. Accessing the dashboard or its WebSocket without a valid token returns 401 — the live transcript cannot be viewed unauthenticated
  2. A webhook request without a valid Twilio signature is rejected before any processing occurs
  3. The `/call` endpoint rejects requests that exceed the configured rate limit per IP
  4. Trace files in `/tmp/shuo/` are bounded — old files are cleaned up automatically, and disk usage does not grow unbounded
**Plans**: TBD

### Phase 6: Agent Framework Migration
**Goal**: The LLM agent uses pydantic-ai with typed tool definitions and structured output, replacing the custom marker scanning protocol
**Depends on**: Phase 1, Phase 2
**Requirements**: AGENT-01, AGENT-02, AGENT-03, AGENT-04, AGENT-05
**Success Criteria** (what must be TRUE):
  1. `LLMAgent` is a pydantic-ai agent — DTMF, hold detection, and hangup are typed tool calls, not marker strings in the text stream
  2. The `MarkerScanner` class is deleted from the codebase
  3. All existing agent behaviors (DTMF navigation, hold detection, hangup) work identically after migration — verified by the existing test suite
  4. The LLM provider (Groq or any OpenAI-compatible endpoint) is selectable via config without code changes
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. ISP Abstraction | 3/3 | Complete    | 2026-03-21 |
| 2. Bug Fixes | 2/2 | Complete    | 2026-03-21 |
| 3. CLI | 2/2 | Complete    | 2026-03-21 |
| 4. IVR Benchmark | 2/3 | In Progress|  |
| 5. Security Hardening | 0/TBD | Not started | - |
| 6. Agent Framework Migration | 0/TBD | Not started | - |
