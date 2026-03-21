# Phase 6: Agent Framework Migration - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

Replace the custom marker scanning protocol with pydantic-ai typed tool calls. Primary targets: `shuo/shuo/services/llm.py` (LLMService internals) and `shuo/shuo/agent.py` (delete MarkerScanner, wire tool callbacks). `conversation.py` and the event system stay unchanged. The result: DTMF, hold state, and hangup are typed tool functions — no marker strings in the text stream.

</domain>

<decisions>
## Implementation Decisions

### TTS streaming strategy

- **Stream text + tools at end** — pydantic-ai `run_stream()` / `stream_text()` delivers text tokens in real-time; tool calls are resolved after the text stream completes. Low-latency TTS is preserved (identical to today).
- **Keep callback interface** — `LLMService` keeps `on_token` / `on_done` callbacks. `Agent.py`'s `_on_llm_token` / `_on_llm_done` remain unchanged. Migration is **internal to `LLMService`** only.
- **Rewrite system prompt from scratch** — New prompt written for tool-calling: describes available tools (`press_dtmf`, `signal_hold`, `signal_hold_end`, `signal_hold_continue`, `signal_hangup`) and when to call them. No legacy marker language.

### Hold mode protocol

- **Keep `[HOLD_CHECK]` message prefix** — When `hold_check=True`, `[HOLD_CHECK]` is still prepended to the transcript in the user message. The LLM calls `signal_hold_continue()` or `signal_hold_end()` tools (instead of emitting `[HOLD_CONTINUE]`/`[HOLD_END]` markers).
- **`signal_hold_continue()` = tool call, no text** — When the agent is still on hold, it calls `signal_hold_continue()` with no accompanying text. `LLMService` detects this tool call, skips TTS, fires `_on_llm_done`. Identical behavior to today.

### Tool API shape

- **Separate tool per action** — Each action is its own typed pydantic-ai tool function:
  - `press_dtmf(digit: str)` — press a key on the phone
  - `signal_hold()` — agent is entering hold mode
  - `signal_hold_end()` — real person detected, exit hold
  - `signal_hold_continue()` — still on hold, suppress TTS and end turn silently
  - `signal_hangup()` — end the call (two-step: confirm first, then hangup in a separate turn)
- **No AgentResponse dataclass** — Tool calls are side-effecting callbacks registered on the pydantic-ai agent via `@agent.tool`. No accumulator class. AGENT-02 is satisfied by the typed tool function signatures themselves.

### Provider & model configuration

- **Single `LLM_MODEL` env var with provider prefix** — Format: `groq:llama-3.3-70b-versatile` (default). The prefix selects the pydantic-ai provider class; the suffix is the model name. Changing provider + model = one env var.
- **Keep `GROQ_API_KEY` as-is** — pydantic-ai reads `GROQ_API_KEY` natively when provider is `groq`. For other providers, standard per-provider key env vars apply (`OPENAI_API_KEY`, etc.). No generic `LLM_API_KEY` override.

### Claude's Discretion

- Exact pydantic-ai agent construction (`Agent(model, tools=[...])` vs dependency injection pattern)
- How tool side effects are passed back to `Agent.py` — e.g. via a shared context object or via the tool return value triggering a callback
- How to parse `LLM_MODEL` env var into a pydantic-ai model instance (e.g. `GroqModel`, `OpenAIModel`)
- Whether `max_tokens` and `temperature` remain configurable or are hardcoded defaults
- Test isolation approach for pydantic-ai agent in existing test suite

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Agent requirements
- `.planning/REQUIREMENTS.md` §Agent Framework Migration — AGENT-01 through AGENT-05: formal requirements with acceptance criteria

### Current implementation (primary migration targets)
- `shuo/shuo/services/llm.py` — `LLMService`: OpenAI streaming client, `on_token`/`on_done` callbacks, history management, system prompt; this file is fully replaced
- `shuo/shuo/agent.py` — `Agent` + `MarkerScanner`: per-turn lifecycle, marker scanning logic, tool-call dispatch post-migration, DTMF/hold/hangup handling
- `shuo/shuo/conversation.py` — `run_conversation()`: event loop that calls `agent.start_turn()`; must remain unchanged after migration

### Event system (must still be emitted correctly)
- `shuo/shuo/types.py` — `DTMFToneEvent`, `HoldStartEvent`, `HoldEndEvent`, `HangupPendingEvent`, `HangupRequestEvent`, `AgentTurnDoneEvent` — these events must fire with identical semantics after migration

### Test suite (must pass after migration)
- `shuo/tests/` — all existing tests must pass; pay special attention to `test_isp.py`, `test_ivr_barge_in.py`, `test_bench.py` which exercise agent behavior

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `LLMService._history` — conversation history as `List[Dict[str, str]]`; pydantic-ai manages its own message history but the format must remain compatible with `Agent.restore_history()` and `Agent.history` property used by `conversation.py`
- `SYSTEM_PROMPT` in `llm.py` — rewritten from scratch, but the goal-suffix injection pattern (`goal_suffix = ... if goal else ""`) is worth preserving for the new prompt
- `generate_dtmf_ulaw_b64` in `services/dtmf.py` — still used post-migration; `press_dtmf` tool handler calls this
- `MarkerScanner.KNOWN` set — documents the full set of marker types; use as the tool inventory checklist during migration

### Established Patterns
- `asyncio.create_task()` for background generation — `LLMService._task` pattern; pydantic-ai's async run wraps cleanly in a task the same way
- `os.getenv()` for all credentials — `LLM_MODEL` default `"groq:llama-3.3-70b-versatile"` follows this pattern
- `on_token` / `on_done` callback wiring — defined in `Agent.__init__`, passed to `LLMService`; migration preserves this boundary

### Integration Points
- `Agent._on_llm_token(token: str)` — receives text tokens from `LLMService`; pydantic-ai `stream_text()` yields equivalent text chunks
- `Agent._on_llm_done()` — fires after text stream ends; tool call side effects (DTMF queue, hold flags, hangup flag) must be populated BEFORE this is called
- `Agent._scanner = MarkerScanner()` (line 165) + all `_pending_*` flags — replaced by tool call callbacks that set the same flags directly, without a scanner
- `Agent.start_turn()` resets `_scanner`, `_dtmf_queue`, `_pending_hold_start`, `_pending_hold_end`, `_pending_hangup` each turn — these resets remain; scanner reset is removed

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard pydantic-ai patterns.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 06-agent-framework-migration*
*Context gathered: 2026-03-22*
