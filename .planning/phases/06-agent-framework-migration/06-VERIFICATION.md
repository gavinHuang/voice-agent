---
phase: 06-agent-framework-migration
verified: 2026-03-22T00:00:00Z
status: passed
score: 9/9 must-haves verified
re_verification: false
---

# Phase 6: Agent Framework Migration Verification Report

**Phase Goal:** The LLM agent uses pydantic-ai with typed tool definitions and structured output, replacing the custom marker scanning protocol
**Verified:** 2026-03-22
**Status:** PASSED
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #  | Truth                                                                                          | Status     | Evidence                                                                |
|----|-----------------------------------------------------------------------------------------------|------------|-------------------------------------------------------------------------|
| 1  | LLMService uses pydantic-ai Agent with iter() for streaming text and executing tool calls     | VERIFIED   | `_agent.iter()` with `ModelRequestNode`/`CallToolsNode` in `_generate()` (llm.py:206-228) |
| 2  | Five typed tool functions registered: press_dtmf, signal_hold, signal_hold_end, signal_hold_continue, signal_hangup | VERIFIED | All five `@self._agent.tool` decorated functions in llm.py:120-148 |
| 3  | LLM_MODEL env var with provider prefix selects the pydantic-ai model                         | VERIFIED   | `os.getenv("LLM_MODEL", "groq:llama-3.3-70b-versatile")` at llm.py:102; tests test_llm_model_groq_prefix and test_llm_model_openai_prefix pass |
| 4  | on_token and on_done callbacks still fire with identical semantics                            | VERIFIED   | Constructor signature unchanged (on_token, on_done, goal); `await self._on_token(token)` in iter loop; `await self._on_done()` after run |
| 5  | MarkerScanner class is deleted from agent.py                                                  | VERIFIED   | `grep "class MarkerScanner" shuo/shuo/agent.py` returns no matches; test_marker_scanner_deleted passes |
| 6  | Agent._on_llm_token forwards text directly to TTS without scanning                           | VERIFIED   | `_on_llm_token` sends token directly to `self._tts.send(token)` with no scanner calls (agent.py:237) |
| 7  | Agent._on_llm_done reads tool side effects from llm.turn_context instead of scanning markers | VERIFIED   | `ctx = self._llm.turn_context` then reads `ctx.dtmf_queue`, `ctx.hold_continue`, `ctx.hold_start`, `ctx.hold_end`, `ctx.hangup_pending` (agent.py:250-295) |
| 8  | DTMFToneEvent, HoldStartEvent, HoldEndEvent, HangupPendingEvent, HangupRequestEvent fire with identical semantics | VERIFIED | All five event emissions present in agent.py; full test suite 113/113 passing |
| 9  | All existing tests pass including test_bug_fixes.py and test_ivr_barge_in.py                 | VERIFIED   | `python -m pytest tests/ -q` returns 113 passed, 0 failed                |

**Score:** 9/9 truths verified

### Required Artifacts

| Artifact                           | Expected                                            | Status     | Details                                                    |
|------------------------------------|-----------------------------------------------------|------------|------------------------------------------------------------|
| `shuo/shuo/services/llm.py`        | pydantic-ai based LLMService with typed tools       | VERIFIED   | 244 lines; contains `from pydantic_ai`, `LLMTurnContext`, all five tools, `Agent(...)`, `iter()` streaming |
| `shuo/tests/test_agent.py`         | Test scaffold for all AGENT requirements            | VERIFIED   | 245 lines; 8 test functions collected; all 8 pass          |
| `shuo/pyproject.toml`              | pydantic-ai-slim[groq] dependency                   | VERIFIED   | `"pydantic-ai-slim[groq]>=0.2.0"` present at line 17      |
| `shuo/shuo/agent.py`               | Agent without MarkerScanner, using LLMTurnContext   | VERIFIED   | 347 lines; no MarkerScanner, no _scanner, no _pending_hold_start/_pending_hold_end; reads `self._llm.turn_context` |
| `shuo/tests/test_bug_fixes.py`     | Updated BUG-03 test without _scanner mock           | VERIFIED   | No `mock_scanner` or `_scanner` references; test_token_observer_nonblocking passes |

### Key Link Verification

| From                              | To                       | Via                                           | Status   | Details                                                        |
|-----------------------------------|--------------------------|-----------------------------------------------|----------|----------------------------------------------------------------|
| `shuo/shuo/services/llm.py`       | `pydantic_ai.Agent`      | Agent constructor with model string            | WIRED    | `self._agent: Agent[LLMTurnContext, str] = Agent(model=_model_string, ...)` at llm.py:108 |
| `shuo/shuo/services/llm.py`       | on_token callback        | PartDeltaEvent streaming in iter() loop        | WIRED    | `await self._on_token(token)` at llm.py:224 inside ModelRequestNode stream |
| `shuo/shuo/services/llm.py`       | LLMTurnContext           | deps= parameter in agent.iter()                | WIRED    | `async with self._agent.iter(self._pending_message, deps=self._turn_ctx, ...)` at llm.py:206 |
| `shuo/shuo/agent.py`              | `shuo/shuo/services/llm.py` | self._llm.turn_context in _on_llm_done      | WIRED    | `ctx = self._llm.turn_context` at agent.py:250                |
| `shuo/shuo/agent.py`              | DTMFToneEvent            | self._emit(DTMFToneEvent(digits=...))          | WIRED    | `self._emit(DTMFToneEvent(digits=digits))` at agent.py:288    |
| `shuo/shuo/agent.py`              | HangupPendingEvent       | self._emit(HangupPendingEvent()) in _on_llm_done | WIRED  | `self._emit(HangupPendingEvent())` at agent.py:275            |

### Requirements Coverage

| Requirement | Source Plan | Description                                                                 | Status     | Evidence                                                                     |
|-------------|-------------|-----------------------------------------------------------------------------|------------|------------------------------------------------------------------------------|
| AGENT-01    | 06-01-PLAN  | LLMAgent migrated to pydantic-ai with typed tool definitions               | SATISFIED  | `from pydantic_ai import Agent, RunContext` in llm.py; Agent constructed with `deps_type=LLMTurnContext` |
| AGENT-02    | 06-01-PLAN  | [DTMF:N], [HOLD], [HANGUP] markers replaced by structured AgentResponse type | SATISFIED | Five `@self._agent.tool` functions replace all marker paths; LLMTurnContext fields replace marker state |
| AGENT-03    | 06-02-PLAN  | MarkerScanner removed after migration                                       | SATISFIED  | No `class MarkerScanner` anywhere in codebase; test_marker_scanner_deleted passes |
| AGENT-04    | 06-02-PLAN  | All existing agent behaviors work identically after migration               | SATISFIED  | 113/113 tests pass; all five event types still emitted in agent.py          |
| AGENT-05    | 06-01-PLAN  | LLM provider configurable via pydantic-ai model selection                  | SATISFIED  | `LLM_MODEL` env var parsed at LLMService init; test_llm_model_groq_prefix and test_llm_model_openai_prefix pass |

No orphaned requirements. All five AGENT requirements (AGENT-01 through AGENT-05) are claimed by plans 06-01 and 06-02 and verified against the codebase.

### Anti-Patterns Found

No blockers or warnings found.

| File | Pattern | Severity | Result |
|------|---------|----------|--------|
| `shuo/shuo/services/llm.py` | TODO/FIXME/PLACEHOLDER | Checked | None found |
| `shuo/shuo/agent.py` | TODO/FIXME/PLACEHOLDER | Checked | None found |
| `shuo/shuo/services/llm.py` | `run_stream` (deprecated API) | Checked | None found — uses `iter()` only |
| `shuo/shuo/services/llm.py` | `AsyncOpenAI` (old client) | Checked | None found — fully replaced |
| `shuo/shuo/services/llm.py` | Marker language `[DTMF:`, `[HOLD]`, `[HANGUP]` | Checked | None found |
| `shuo/shuo/agent.py` | `_scanner`, `MarkerScanner` | Checked | None found |

### Human Verification Required

None. All goal-level behaviors are verifiable programmatically through the test suite.

The full suite (113 tests) exercises:
- pydantic-ai streaming via TestModel
- All five tool side effects (dtmf_queue, hold_start, hold_end, hold_continue, hangup_pending)
- MarkerScanner deletion
- LLM_MODEL env var provider prefix selection
- BUG-03 non-blocking observer (preserved after migration)
- IVR barge-in behavior (test_ivr_barge_in.py, unaffected)

### Gaps Summary

No gaps. All must-haves from both plans are satisfied.

## Commits Verified

| Commit  | Description                                                      |
|---------|------------------------------------------------------------------|
| deba1af | test(06-01): add failing test scaffold for pydantic-ai LLMService |
| c2c4eb8 | feat(06-01): install pydantic-ai and rewrite LLMService with typed tools |
| b07b70b | feat(06-02): remove MarkerScanner, wire Agent to LLMTurnContext  |
| 9320f5c | feat(06-02): remove scanner mock from test_bug_fixes, full suite green |

---

_Verified: 2026-03-22_
_Verifier: Claude (gsd-verifier)_
