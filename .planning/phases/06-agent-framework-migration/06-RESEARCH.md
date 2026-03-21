# Phase 6: Agent Framework Migration - Research

**Researched:** 2026-03-22
**Domain:** pydantic-ai agent framework, streaming with tool calls, Groq provider, conversation history migration
**Confidence:** HIGH (primary sources: official pydantic-ai docs, PyPI registry, GitHub issues)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**TTS streaming strategy:**
- Stream text + tools at end — pydantic-ai `run_stream()` / `stream_text()` delivers text tokens in real-time; tool calls are resolved after the text stream completes. Low-latency TTS is preserved.
- Keep callback interface — `LLMService` keeps `on_token` / `on_done` callbacks. `Agent.py`'s `_on_llm_token` / `_on_llm_done` remain unchanged. Migration is internal to `LLMService` only.
- Rewrite system prompt from scratch — New prompt for tool-calling describes available tools and when to call them. No legacy marker language.

**Hold mode protocol:**
- Keep `[HOLD_CHECK]` message prefix — prepended to transcript in user message when `hold_check=True`. LLM calls `signal_hold_continue()` or `signal_hold_end()` tools instead of emitting markers.
- `signal_hold_continue()` = tool call, no text — `LLMService` detects this tool call, skips TTS, fires `_on_llm_done`. Identical behavior to today.

**Tool API shape:**
- Separate tool per action: `press_dtmf(digit: str)`, `signal_hold()`, `signal_hold_end()`, `signal_hold_continue()`, `signal_hangup()`
- No AgentResponse dataclass — Tool calls are side-effecting callbacks registered on the pydantic-ai agent via `@agent.tool`. No accumulator class.

**Provider & model configuration:**
- Single `LLM_MODEL` env var with provider prefix — Format: `groq:llama-3.3-70b-versatile` (default).
- Keep `GROQ_API_KEY` as-is — pydantic-ai reads `GROQ_API_KEY` natively.

### Claude's Discretion

- Exact pydantic-ai agent construction (`Agent(model, tools=[...])` vs dependency injection pattern)
- How tool side effects are passed back to `Agent.py` — via a shared context object or via tool return value triggering a callback
- How to parse `LLM_MODEL` env var into a pydantic-ai model instance
- Whether `max_tokens` and `temperature` remain configurable or are hardcoded defaults
- Test isolation approach for pydantic-ai agent in existing test suite

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| AGENT-01 | `LLMAgent` is migrated to pydantic-ai with typed tool definitions | pydantic-ai 1.70.0 `@agent.tool` pattern; `Agent('groq:...')` constructor |
| AGENT-02 | `[DTMF:N]`, `[HOLD]`, `[HANGUP]` markers replaced by structured tool calls | Five typed tools replacing `MarkerScanner.KNOWN` set; typed function signatures ARE the structure |
| AGENT-03 | `MarkerScanner` is removed after migration | Tool callbacks set `_pending_*` flags directly; scanner class deleted entirely |
| AGENT-04 | All existing agent behaviors (DTMF, hold detection, hangup) work identically | `_on_llm_token` / `_on_llm_done` callbacks preserved; tool side effects populate same flags |
| AGENT-05 | LLM provider (Groq/OpenAI-compatible) is configurable via pydantic-ai model selection | `LLM_MODEL` env var parsed into pydantic-ai model using `'provider:model'` string syntax |
</phase_requirements>

---

## Summary

pydantic-ai 1.70.0 (latest, released 2026) provides a clean migration path from the custom marker-scanning protocol. The core pattern is: replace `openai.AsyncOpenAI` streaming with `agent.run_stream()` + `stream_text(delta=True)`, and replace `MarkerScanner` with `@agent.tool`-decorated functions that mutate a shared `RunContext` dependencies object. The `on_token` / `on_done` callback boundary at `LLMService` is preserved — `agent.py` is untouched.

**Critical architectural finding:** `run_stream()` + `stream_text()` with tools has a known limitation — if the model emits text before a tool call in the same response, the tool call is silently dropped (end_strategy='early' treats the text as the final output). The recommended alternative is `agent.iter()`, which properly handles the ModelRequestNode (text streaming) and CallToolsNode (tool execution) as separate graph steps. This is the pattern to use. The maintainers note `run_stream()` is being deprecated in favor of `iter()`.

**Primary recommendation:** Use `agent.iter()` with `ModelRequestNode.stream()` for text token streaming and `CallToolsNode.stream()` for tool execution. This correctly handles text-before-tools, is the current recommended pattern, and maps naturally to the `on_token` / `on_done` callback structure.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pydantic-ai | 1.70.0 | LLM agent framework with typed tools | Official recommendation; replaces manual OpenAI streaming + MarkerScanner |
| pydantic-ai-slim[groq] | 1.70.0 | Slim distribution with Groq extras | Includes `groq>=0.25.0`; avoids pulling all provider dependencies |
| groq | >=0.25.0 | Groq async client (pulled by pydantic-ai-slim[groq]) | Native pydantic-ai Groq integration reads GROQ_API_KEY automatically |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| openai (existing) | >=1.0.0 | OpenAI-compatible provider fallback | When `LLM_MODEL` starts with `openai:` |
| pytest-asyncio (existing) | >=0.21.0 | Async test support | All new LLMService tests use `@pytest.mark.asyncio` |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| pydantic-ai-slim[groq] | pydantic-ai (full) | Full package pulls all provider libs; slim is sufficient |
| agent.iter() | agent.run_stream() | run_stream() drops tool calls when text precedes them; iter() is correct |
| @agent.tool (decorator) | tools=[...] constructor arg | Equivalent; decorator pattern is cleaner when tools are defined near the agent |

**Installation:**
```bash
pip install "pydantic-ai-slim[groq]>=1.70.0"
```

Add to `requirements.txt` and `pyproject.toml` dependencies. The existing `openai>=1.0.0` dependency stays (used by TwilioISP and as OpenAI-compatible fallback).

**Version verification:** Confirmed 1.70.0 on PyPI as of 2026-03-22. Requires Python >=3.10 (project uses 3.12).

---

## Architecture Patterns

### Recommended Project Structure

No new directories needed. Migration is internal to `shuo/shuo/services/llm.py`:

```
shuo/shuo/
├── agent.py              # Remove MarkerScanner class; remove self._scanner reset; keep all else
├── services/
│   └── llm.py            # Full rewrite: pydantic-ai Agent, iter()-based streaming, tool callbacks
└── types.py              # Unchanged
```

### Pattern 1: Shared Tool Context via RunContext Deps

Tool side effects must reach `Agent.py`'s `_pending_*` flags. The canonical pydantic-ai pattern is to pass a mutable dataclass as `deps`.

**What:** Define a `LLMTurnContext` dataclass with the same flag fields currently spread across `Agent._pending_*`. `LLMService.start()` creates a fresh context per turn and passes it as `deps=`. After the run completes, `LLMService._on_done` reads the flags and fires them back.

**When to use:** Any time tools need to communicate results to the caller without changing tool return values.

```python
# Source: https://ai.pydantic.dev/dependencies/
from dataclasses import dataclass, field
from typing import List

@dataclass
class LLMTurnContext:
    dtmf_queue: List[str] = field(default_factory=list)
    hold_start: bool = False
    hold_end: bool = False
    hold_continue: bool = False
    hangup_pending: bool = False
```

Tools mutate `ctx.deps` directly:
```python
@agent.tool
async def press_dtmf(ctx: RunContext[LLMTurnContext], digit: str) -> str:
    """Press a DTMF key on the phone."""
    ctx.deps.dtmf_queue.append(digit)
    return f"DTMF {digit} queued"

@agent.tool_plain
async def signal_hangup() -> str:
    """Signal that the call should end."""
    # Note: tool_plain has no ctx; use @agent.tool for side effects
    ...
```

After the run, `LLMService` reads `ctx.deps` and fires the appropriate `Agent.py` callbacks.

### Pattern 2: iter() for Streaming Text + Tool Execution

**What:** Use `agent.iter()` as an async context manager. Iterate over nodes. On `ModelRequestNode`, stream text tokens. On `CallToolsNode`, allow tools to execute (they run automatically inside `node.stream()`).

**When to use:** Any time you need text streaming AND tool execution in the same agent run.

```python
# Source: https://ai.pydantic.dev/agent/#iterating-over-an-agents-graph
from pydantic_ai import Agent
from pydantic_ai.nodes import ModelRequestNode, CallToolsNode
from pydantic_ai.messages import PartDeltaEvent, TextPartDelta

async with agent.iter(user_message, deps=turn_ctx, message_history=history) as run:
    async for node in run:
        if Agent.is_model_request_node(node):
            # Stream text tokens to on_token callback
            async with node.stream(run.ctx) as stream:
                async for event in stream:
                    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                        await self._on_token(event.delta.content_delta)
        elif Agent.is_call_tools_node(node):
            # Tools execute automatically during this iteration
            async with node.stream(run.ctx) as stream:
                async for _ in stream:
                    pass  # tool side effects happen via ctx.deps mutation

# After run: read turn_ctx for tool results, fire _on_done
await self._on_done()
```

### Pattern 3: Model String Parsing

**What:** Parse `LLM_MODEL` env var (format: `provider:model-name`) into a pydantic-ai model.

**When to use:** In `LLMService.__init__()`.

```python
# Source: https://ai.pydantic.dev/models/
import os
from pydantic_ai import Agent

model_string = os.getenv("LLM_MODEL", "groq:llama-3.3-70b-versatile")
# pydantic-ai natively parses "provider:model-name" strings
# Supported prefixes: groq, openai, anthropic, gemini, etc.
agent = Agent(model_string, ...)
```

pydantic-ai resolves the provider class automatically from the prefix. `groq:` uses `GroqModel`; `openai:` uses `OpenAIChatModel`. GROQ_API_KEY is read from env automatically when provider is `groq`.

### Pattern 4: Message History Format

pydantic-ai uses its own `ModelMessage` type (not OpenAI dicts). The history passed to `message_history=` must be a `Sequence[ModelMessage]`.

**Critical compatibility note:** `Agent.history` (and `LLMService.history`) currently returns `List[Dict[str, str]]` (OpenAI format). `conversation.py` stores this via `agent.restore_history()` and reads it back. After migration, pydantic-ai's `result.new_messages()` returns `List[ModelMessage]` — NOT the old dict format.

**Required approach:** Store pydantic-ai `ModelMessage` objects natively. `LLMService._history` becomes `List[ModelMessage]`. The `Agent.history` property returns `List[ModelMessage]`. `Agent.restore_history()` accepts `List[ModelMessage]`. All existing callers (only `conversation.py`, only via `_dtmf_pending` dict) must store the pydantic-ai format.

Serialize/deserialize using pydantic-ai's `ModelMessagesTypeAdapter`:
```python
# Source: https://ai.pydantic.dev/message-history/
from pydantic_ai.messages import ModelMessagesTypeAdapter

# Serialize to JSON for storage
json_bytes = ModelMessagesTypeAdapter.dump_json(messages)

# Deserialize from JSON
messages = ModelMessagesTypeAdapter.validate_json(json_bytes)
```

### Pattern 5: Agent Construction with Tools

```python
# Source: https://ai.pydantic.dev/api/agent/ (constructor signature)
from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

agent = Agent(
    model=model_string,               # e.g. "groq:llama-3.3-70b-versatile"
    deps_type=LLMTurnContext,
    system_prompt=SYSTEM_PROMPT,
    model_settings=ModelSettings(
        max_tokens=500,
        temperature=0.7,
    ),
)

@agent.tool
async def press_dtmf(ctx: RunContext[LLMTurnContext], digit: str) -> str: ...

@agent.tool
async def signal_hold(ctx: RunContext[LLMTurnContext]) -> str: ...

@agent.tool
async def signal_hold_end(ctx: RunContext[LLMTurnContext]) -> str: ...

@agent.tool
async def signal_hold_continue(ctx: RunContext[LLMTurnContext]) -> str: ...

@agent.tool
async def signal_hangup(ctx: RunContext[LLMTurnContext]) -> str: ...
```

### Anti-Patterns to Avoid

- **Using `run_stream()` + `stream_text()` with tools:** Text before a tool call causes tool silencing. The pydantic-ai team recommends migrating to `iter()`.
- **Using `end_strategy='exhaustive'` as a workaround:** Still broken in streaming mode; iter() is the fix.
- **Storing tool state in tool return values:** Use `ctx.deps` mutation instead; return values go to the LLM, not to the caller.
- **Rebuilding `openai.AsyncOpenAI` client inside LLMService:** pydantic-ai manages its own client lifecycle; don't wrap it.
- **Per-turn Agent instantiation:** The pydantic-ai `Agent` is stateless (no per-turn history); create it once at `LLMService.__init__()`. History is passed at run time via `message_history=`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Tool schema generation | Manual JSON schema dicts | `@agent.tool` decorator | pydantic extracts schema from function signature automatically |
| Provider selection logic | if/elif chains for provider strings | `Agent("provider:model")` string syntax | pydantic-ai resolves provider class natively |
| Message serialization | Custom JSON encoder for history | `ModelMessagesTypeAdapter` | Handles all message types including tool calls/results |
| Tool call retry on bad args | Custom error handling | pydantic-ai built-in validation | Framework catches schema validation errors and lets model retry |
| API key management | Per-provider key lookup | pydantic-ai provider env var convention | `GROQ_API_KEY`, `OPENAI_API_KEY` etc. are read automatically |
| Groq client setup | `groq.AsyncGroq(api_key=...)` directly | `Agent("groq:model")` | pydantic-ai manages the groq client internally |

**Key insight:** pydantic-ai's `Agent` is intentionally stateless between runs — all state (history, deps) is passed at call time. This maps perfectly to `LLMService`'s per-turn `start()` pattern.

---

## Common Pitfalls

### Pitfall 1: Tool Calls Dropped in run_stream()
**What goes wrong:** Tool calls are silently not executed. The agent appears to respond but DTMF/hold/hangup signals never fire.
**Why it happens:** `run_stream()` treats the first output matching `output_type` (text, since `output_type=str`) as final. With `end_strategy='early'` (default), any tool calls after text are skipped.
**How to avoid:** Use `agent.iter()` instead of `run_stream()`. The maintainers are deprecating `run_stream()`.
**Warning signs:** Tool callbacks never called despite correct function definitions; agent responds with text only.

### Pitfall 2: Llama 3.3 Inconsistent Tool Invocation
**What goes wrong:** Occasionally the model returns tool calls as literal text (e.g. `<function=press_dtmf(...)>`) instead of structured tool call format.
**Why it happens:** Llama 3.3 inconsistently follows Groq's tool-calling instruction format. pydantic-ai confirmed this is a model/provider issue, not a framework bug.
**How to avoid:** System prompt must be extremely clear about when to use tools vs text. Prefer llama-3.3-70b-versatile (most capable on Groq). Consider retry logic or fallback parsing.
**Warning signs:** Tool handler never invoked; raw function-call syntax appears in TTS audio stream.

### Pitfall 3: History Format Incompatibility with conversation.py
**What goes wrong:** `server.py` saves `agent.history` to `_dtmf_pending` dict on DTMF, then `Agent.restore_history()` reloads it. If the format changes from OpenAI dicts to pydantic-ai `ModelMessage` objects, deserialization breaks across sessions.
**Why it happens:** The takeover-and-reconnect flow in `conversation.py` stores history as JSON (implicitly via `_dtmf_pending[call_sid] = {"history": agent.history, ...}`). pydantic-ai `ModelMessage` objects are not plain dicts.
**How to avoid:** Audit `server.py` for all `agent.history` reads/writes. Use `ModelMessagesTypeAdapter.dump_json()` when storing and `validate_json()` when loading. Update `Agent.restore_history()` signature to accept `List[ModelMessage]`.
**Warning signs:** `TypeError` or `ValidationError` when restoring history after a DTMF handoff.

### Pitfall 4: signal_hold_continue Triggers TTS
**What goes wrong:** `hold_continue` still plays silence or empty audio; the call loop stalls waiting for `AgentTurnDoneEvent`.
**Why it happens:** The flag is only set by the tool, but `_on_llm_done` still takes the `tts_had_text=True` path if any token came before the tool call.
**How to avoid:** `signal_hold_continue` tool sets `ctx.deps.hold_continue = True`. `LLMService._on_done` MUST check `hold_continue` FIRST, before checking `tts_had_text`, and call `tts.cancel()` + fire `_on_llm_done` immediately — identical to current behavior.
**Warning signs:** Silence played to user on hold; AgentTurnDoneEvent fires late.

### Pitfall 5: HangupPendingEvent Must Fire Mid-Streaming
**What goes wrong:** Hangup confirmation response starts playing but new turns aren't blocked until playback finishes.
**Why it happens:** Currently `HangupPendingEvent` is emitted inside `_on_llm_token` when `[HANGUP]` marker appears mid-stream. With tools, `signal_hangup` fires after text streaming completes (in `CallToolsNode`). This is acceptable IF `_on_llm_done` fires the event before TTS flush.
**How to avoid:** In `_on_llm_done`, after reading `ctx.deps.hangup_pending = True`, emit `HangupPendingEvent()` before calling `tts.flush()`. The timing is slightly later than today (post-text, not mid-stream) but functionally equivalent.
**Warning signs:** New turn starts during the goodbye sentence.

### Pitfall 6: pydantic-ai Requires Python >= 3.10
**What goes wrong:** Import fails or type annotations don't work as expected.
**Why it happens:** pydantic-ai 1.x requires Python >=3.10. Project uses Python 3.12 — no issue.
**How to avoid:** Verify CI runs Python 3.10+. This project is already on 3.12.

### Pitfall 7: test_bug_fixes.py Uses `agent._scanner`
**What goes wrong:** `test_token_observer_nonblocking` manually sets `agent._scanner = mock_scanner` which won't exist post-migration.
**Why it happens:** The test directly constructs an Agent instance via `Agent.__new__(Agent)` and patches internal state.
**How to avoid:** Update the test to mock `LLMService` instead (or set `agent._pending_*` flags directly and remove the scanner mock line). The BUG-03 behavior being tested (non-blocking observer) is in `_on_llm_token`, which is unchanged — only `_scanner.feed()` invocation is removed.

---

## Code Examples

Verified patterns from official sources:

### Agent Construction with Groq
```python
# Source: https://ai.pydantic.dev/models/groq/
import os
from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

model_string = os.getenv("LLM_MODEL", "groq:llama-3.3-70b-versatile")

agent = Agent(
    model=model_string,
    deps_type=LLMTurnContext,
    system_prompt=SYSTEM_PROMPT,
    model_settings=ModelSettings(max_tokens=500, temperature=0.7),
)
```

### Tool Definition with Deps Mutation
```python
# Source: https://ai.pydantic.dev/tools/
from pydantic_ai import Agent, RunContext

@agent.tool
async def press_dtmf(ctx: RunContext[LLMTurnContext], digit: str) -> str:
    """Press a DTMF digit on the phone keypad. Use for IVR menu navigation."""
    ctx.deps.dtmf_queue.append(digit)
    return f"DTMF digit {digit!r} will be sent"

@agent.tool
async def signal_hold(ctx: RunContext[LLMTurnContext]) -> str:
    """Signal that hold music has been detected."""
    ctx.deps.hold_start = True
    return "Hold mode activated"

@agent.tool
async def signal_hold_continue(ctx: RunContext[LLMTurnContext]) -> str:
    """Signal that hold music is still playing — no spoken response needed."""
    ctx.deps.hold_continue = True
    return "Continuing to wait on hold"

@agent.tool
async def signal_hold_end(ctx: RunContext[LLMTurnContext]) -> str:
    """Signal that a real person has returned from hold."""
    ctx.deps.hold_end = True
    return "Hold ended, person detected"

@agent.tool
async def signal_hangup(ctx: RunContext[LLMTurnContext]) -> str:
    """Signal that the call should be hung up after this response completes."""
    ctx.deps.hangup_pending = True
    return "Call will end after this response"
```

### Streaming with iter()
```python
# Source: https://ai.pydantic.dev/agent/#iterating-over-an-agents-graph
from pydantic_ai import Agent
from pydantic_ai.messages import PartDeltaEvent, TextPartDelta

async def _generate(self) -> None:
    turn_ctx = LLMTurnContext()
    assistant_text = ""

    async with agent.iter(
        user_message,
        deps=turn_ctx,
        message_history=self._history,
    ) as run:
        async for node in run:
            if Agent.is_model_request_node(node):
                async with node.stream(run.ctx) as stream:
                    async for event in stream:
                        if (
                            isinstance(event, PartDeltaEvent)
                            and isinstance(event.delta, TextPartDelta)
                            and event.delta.content_delta
                        ):
                            token = event.delta.content_delta
                            assistant_text += token
                            await self._on_token(token)
            elif Agent.is_call_tools_node(node):
                async with node.stream(run.ctx) as stream:
                    async for _ in stream:
                        pass  # tools execute; ctx.deps gets mutated

    # Persist new messages to history
    self._history = list(run.result.all_messages())

    # Dispatch tool side effects then signal done
    await self._dispatch_tool_effects(turn_ctx)
    await self._on_done()
```

### History Serialization for takeover handoff
```python
# Source: https://ai.pydantic.dev/message-history/
from pydantic_ai.messages import ModelMessagesTypeAdapter

# In LLMService — returning history for server storage
def get_history_json(self) -> bytes:
    return ModelMessagesTypeAdapter.dump_json(self._history)

# In LLMService — restoring history after DTMF reconnect
def set_history_json(self, data: bytes) -> None:
    self._history = ModelMessagesTypeAdapter.validate_json(data)
```

### Testing with TestModel
```python
# Source: https://ai.pydantic.dev/testing/
from pydantic_ai.models.test import TestModel

def test_dtmf_tool_called():
    with agent.override(model=TestModel()):
        # TestModel auto-calls all registered tools
        result = agent.run_sync("Press 1 for sales")
        # Assertions on tool side effects
```

### ModelSettings for max_tokens / temperature
```python
# Source: https://ai.pydantic.dev/api/agent/
from pydantic_ai.settings import ModelSettings

settings = ModelSettings(
    max_tokens=int(os.getenv("LLM_MAX_TOKENS", "500")),
    temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
)
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `run_stream()` + `stream_text()` | `agent.iter()` with node streaming | pydantic-ai ~0.8+ | run_stream() being deprecated; iter() is the supported path |
| `openai.AsyncOpenAI` directly | `Agent("groq:model")` string | This migration | Provider management, key lookup, retries all handled by pydantic-ai |
| MarkerScanner regex parsing | `@agent.tool` typed functions | This migration | Schema-validated, no parsing bugs, model retry on bad args |
| OpenAI dict history format | pydantic-ai `ModelMessage` | This migration | Cross-provider history, tool call records in history |
| `LLM_MODEL` = bare model name | `LLM_MODEL` = `provider:model-name` | This migration | Provider selectable without code changes |

**Deprecated/outdated:**
- `run_stream()`: Being deprecated by pydantic-ai team; use `iter()` instead
- `GROQ_API_KEY` in `AsyncOpenAI(api_key=...)`: No longer needed; pydantic-ai reads it natively for `groq:` models
- `openai` package for LLM calls: Retained only as optional OpenAI-compatible fallback; Groq calls no longer use it

---

## Open Questions

1. **Tool calls that appear without text (DTMF-only turns)**
   - What we know: In the current code, a DTMF-only turn is when `_tts_had_text = False` and `_dtmf_queue` is non-empty in `_on_llm_done`. This path skips TTS and emits `DTMFToneEvent` directly.
   - What's unclear: Will the LLM consistently emit `press_dtmf()` with NO accompanying text when instructed? The system prompt must be very explicit ("respond with ONLY a tool call, no text").
   - Recommendation: Confirm in system prompt that pure tool-call turns (no text) are valid. The `iter()` loop will yield only a `CallToolsNode` pass with no `TextPartDelta` events if the model emits only a tool call. The `assistant_text == ""` check in `_on_llm_done` triggers the DTMF-only path.

2. **History format in `server.py` _dtmf_pending**
   - What we know: `server.py` stores `{"history": agent.history, ...}` in a dict that crosses a DTMF handoff. Currently `agent.history` is `List[Dict]` (JSON-serializable natively).
   - What's unclear: Whether `server.py` JSON-serializes this dict or stores it in-process only.
   - Recommendation: Audit `server.py` for all `_dtmf_pending` writes. If stored in-process only, `List[ModelMessage]` works as-is. If JSON-serialized, must use `ModelMessagesTypeAdapter.dump_json()`.

3. **Groq streaming + tools race condition**
   - What we know: GitHub issue #1714 reports Llama 3.3 sometimes returning literal function-call syntax instead of structured tool calls. pydantic-ai confirmed this is a model/provider behavior issue.
   - What's unclear: Frequency and reproducibility with llama-3.3-70b-versatile specifically.
   - Recommendation: Write tests that mock the model and verify the tool dispatch path. The production behavior depends on Groq's reliability. Consider including a fallback in the system prompt that handles literal text DTMF as secondary parsing.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 0.21.0 |
| Config file | none (pytest auto-discovers) |
| Quick run command | `cd shuo && python -m pytest tests/test_bug_fixes.py tests/test_isp.py -x -q` |
| Full suite command | `cd shuo && python -m pytest tests/ -x -q` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| AGENT-01 | pydantic-ai Agent constructed with GroqModel | unit | `pytest tests/test_llm_service.py -x` | ❌ Wave 0 |
| AGENT-01 | `agent.iter()` streaming yields text tokens | unit | `pytest tests/test_llm_service.py::test_streaming_tokens -x` | ❌ Wave 0 |
| AGENT-02 | press_dtmf tool sets dtmf_queue | unit | `pytest tests/test_llm_service.py::test_tool_press_dtmf -x` | ❌ Wave 0 |
| AGENT-02 | signal_hangup tool sets hangup_pending flag | unit | `pytest tests/test_llm_service.py::test_tool_signal_hangup -x` | ❌ Wave 0 |
| AGENT-03 | MarkerScanner class absent from agent.py | unit | `pytest tests/test_agent_migration.py::test_no_marker_scanner -x` | ❌ Wave 0 |
| AGENT-04 | DTMFToneEvent fired after press_dtmf tool | integration | `pytest tests/test_agent_migration.py::test_dtmf_end_to_end -x` | ❌ Wave 0 |
| AGENT-04 | HoldStartEvent fired after signal_hold tool | integration | `pytest tests/test_agent_migration.py::test_hold_detection -x` | ❌ Wave 0 |
| AGENT-04 | hold_continue path cancels TTS silently | unit | `pytest tests/test_llm_service.py::test_hold_continue_no_tts -x` | ❌ Wave 0 |
| AGENT-04 | Existing BUG-03 test still passes (scanner mock removed) | regression | `pytest tests/test_bug_fixes.py::test_token_observer_nonblocking -x` | ✅ (needs update) |
| AGENT-05 | LLM_MODEL=groq:model → GroqModel | unit | `pytest tests/test_llm_service.py::test_model_string_parsing -x` | ❌ Wave 0 |
| AGENT-05 | LLM_MODEL=openai:gpt-4o → OpenAI model | unit | `pytest tests/test_llm_service.py::test_model_string_openai -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `cd shuo && python -m pytest tests/test_bug_fixes.py tests/test_isp.py tests/test_ivr_barge_in.py -x -q`
- **Per wave merge:** `cd shuo && python -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_llm_service.py` — covers AGENT-01, AGENT-02, AGENT-05 (unit tests for new LLMService with TestModel)
- [ ] `tests/test_agent_migration.py` — covers AGENT-03, AGENT-04 (integration tests for Agent + LLMService wiring)
- [ ] `tests/test_bug_fixes.py::test_token_observer_nonblocking` — update to remove `agent._scanner` mock (line ~229) since MarkerScanner is deleted

Note: `test_ivr_barge_in.py` and `test_bench.py` mock `Agent` entirely (via `patch("shuo.conversation.Agent")`), so they are unaffected by the migration and should pass as-is.

---

## Sources

### Primary (HIGH confidence)
- https://ai.pydantic.dev/agent/ — Agent class, iter() pattern, node streaming API
- https://ai.pydantic.dev/api/agent/ — Full constructor signature, AgentRun class
- https://ai.pydantic.dev/models/groq/ — GroqModel, GROQ_API_KEY env var, model string syntax
- https://ai.pydantic.dev/tools/ — @agent.tool decorator, @agent.tool_plain, RunContext deps pattern
- https://ai.pydantic.dev/message-history/ — ModelMessagesTypeAdapter, history serialization, message_history= parameter
- https://ai.pydantic.dev/api/result/ — StreamedRunResult, stream_text() signature with delta= parameter
- https://ai.pydantic.dev/testing/ — TestModel, FunctionModel, Agent.override() for test isolation
- https://pypi.org/pypi/pydantic-ai/json — Version 1.70.0 confirmed, requires Python >=3.10
- https://pypi.org/pypi/pydantic-ai-slim/json — `groq>=0.25.0` via extras

### Secondary (MEDIUM confidence)
- https://github.com/pydantic/pydantic-ai/issues/1007 — run_stream() deprecation confirmed by maintainer; iter() recommended
- https://github.com/pydantic/pydantic-ai/issues/3574 — Text-before-tool silences tool calls in run_stream(); documented limitation
- https://github.com/pydantic/pydantic-ai/issues/1714 — Llama 3.3 inconsistent tool invocation on Groq; model-level issue not framework bug

### Tertiary (LOW confidence)
- https://datastud.dev/posts/pydantic-ai-streaming/ — iter() pattern example with CallToolsNode (single blog post, pattern verified against official docs)

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — versions confirmed on PyPI; Groq extras dependency confirmed
- Architecture: HIGH — iter() pattern from official docs; run_stream() deprecation from maintainer statement on GitHub
- Pitfalls: HIGH (tool silencing) / MEDIUM (Groq Llama inconsistency — single issue report, no frequency data)
- History format migration: MEDIUM — ModelMessagesTypeAdapter API from official docs; server.py storage behavior requires code audit

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (pydantic-ai releases frequently; check for breaking changes if >30 days)
