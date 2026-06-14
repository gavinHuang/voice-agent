## Context

`voice-agent` (`shuo` package) contains a `LanguageModel` class that handles all LLM interaction: pydantic-ai agent setup, Groq streaming, tool calling, history management, and outcome resolution. `dialact-eval` currently duplicates this entire class as `EvalLanguageModel` and installs `shuo` as a local editable package to share `CallContext`, prompts, and translation helpers.

The duplication creates a maintenance burden: prompt changes, tool definitions, or model-selection logic must be kept in sync across both classes. Installing `shuo` as a dependency also couples dialact-eval's runtime to voice-agent's full dependency tree (Twilio SDK, Deepgram, etc.).

## Goals / Non-Goals

**Goals:**
- Expose voice-agent's `LanguageModel` over HTTP so dialact-eval can drive LLM turns without importing `shuo`.
- Support both blocking `generate` and token-streaming (for the chat UI) over HTTP.
- Keep dialact-eval's call sites (`runner.py`, `ui/app.py`) unchanged — same method signatures.
- Remove `shuo` from dialact-eval's dependencies entirely.

**Non-Goals:**
- Persistent session storage across voice-agent restarts (in-memory only; sessions are ephemeral eval artifacts).
- Authentication / rate-limiting on the `/llm` endpoints (internal eval use only).
- Changing how voice-agent's production call path (`/ws`, `/call`) works.
- Migrating `deepeval` metrics or report generation to voice-agent.

## Decisions

### D1: REST sessions + SSE streaming (not WebSocket)

**Decision:** Use REST for session lifecycle and generate, and Server-Sent Events (SSE) for streaming tokens.

**Rationale:** SSE is simpler than WebSocket for a unidirectional token stream (server → client). The client sends one message per turn (POST body), and the server streams tokens back — no bidirectional protocol needed. `httpx` supports SSE natively via `iter_lines()`. WebSocket would add connection-upgrade complexity with no benefit for this use case.

**Alternatives considered:**
- WebSocket: more complex, unnecessary for unidirectional streaming.
- Long-polling: poor latency for token-by-token streaming.

### D2: `LLMClient` wraps HTTP, preserving `EvalLanguageModel` interface

**Decision:** Replace `EvalLanguageModel` in `dialact-eval/core/language.py` with `LLMClient` that exposes the same `generate()`, `stream_generate()`, and `token_stream()` methods. Callers (`runner.py`, `ui/app.py`) are untouched.

**Rationale:** Minimises diff surface. All three callers already use the existing interface; changing the interface would require updating every call site for no functional gain.

**Alternatives considered:**
- Update runner.py and ui/app.py to call `httpx` directly: larger diff, breaks the clean separation between transport and LLM logic.

### D3: Sessions keyed by UUID, held in voice-agent memory

**Decision:** `POST /llm/sessions` returns a `session_id` (UUID4). voice-agent stores `LanguageModel` instances in a process-level dict keyed by session ID. Sessions are deleted via `DELETE /llm/sessions/{id}` or by voice-agent restart.

**Rationale:** Eval sessions are short-lived (one conversation scenario). No persistence requirement. In-memory is the simplest correct solution.

**Alternatives considered:**
- Redis-backed sessions: operational overhead, unnecessary for eval workloads.

### D4: `CallContext` serialised as JSON in POST body (not re-exported)

**Decision:** `LLMClient` accepts a `CallContext` pydantic model (imported from `shuo`) for `POST /llm/sessions`, serialised as `.model_dump()`. dialact-eval keeps a local copy of the `CallContext` dataclass or continues importing it from a minimal shuo install.

**Refinement:** dialact-eval's `core/context.py` currently re-exports `CallContext` from `shuo.context`. To fully remove the `shuo` dependency, `CallContext` and `build_system_prompt` must either be duplicated locally in dialact-eval or extracted into a shared schema package. For now, dialact-eval keeps a `shuo` dependency **scoped only to context/prompts** (no Twilio/Deepgram transitive deps pulled in) — the heavy coupling through `EvalLanguageModel` is removed.

**Alternative considered:** Extract `CallContext` + prompts into a third `shuo-schemas` package — deferred as over-engineering for current scale.

### D5: New router in `shuo/llm_api.py`, mounted in `web.py`

**Decision:** All `/llm/*` endpoints live in a new `shuo/llm_api.py` FastAPI `APIRouter`, included in `web.py` with `app.include_router(llm_router, prefix="/llm")`.

**Rationale:** Keeps `web.py` from growing further; mirrors the existing pattern (monitor, ttft are also separate modules).

## Risks / Trade-offs

- **Memory leak** if callers never `DELETE` a session → Mitigation: add a background task that prunes sessions idle > N minutes (configurable, default 30m).
- **Concurrency**: `LanguageModel` holds mutable history; two concurrent `generate` calls on the same session would corrupt state → Mitigation: per-session `asyncio.Lock` acquired for the duration of each turn.
- **SSE client errors**: if `httpx` SSE connection drops mid-stream, dialact-eval gets a partial response → Mitigation: `LLMClient.stream_generate` raises on connection error; runner treats it as a failed turn.
- **Dependency scope**: dialact-eval still imports `shuo` for `CallContext` — a future `shuo-schemas` extraction would fully decouple them. Acceptable for now.

## Migration Plan

1. Add `shuo/llm_api.py` + mount in `web.py` (voice-agent change, backward-compatible — new routes only).
2. Replace `dialact-eval/core/language.py` (`EvalLanguageModel` → `LLMClient`).
3. Update `dialact-eval/pyproject.toml`: add `httpx[http2]`, keep `shuo` (scoped).
4. Smoke-test: run `dialact-eval` chat UI against local voice-agent, run one eval scenario.
5. No rollback needed — the old `EvalLanguageModel` can be restored from git if needed.

## Open Questions

- Should `/llm/sessions` also accept a plain `goal` string (not a full `CallContext`) for quick experiments? Deferred — callers always have a `CallContext` today.
- Should the SSE stream include a final `event: done` frame with the full `TurnResult` JSON, or should callers call a separate `GET /llm/sessions/{id}/result` after streaming? → Decided: include a final `data: {"type":"done", ...TurnResult}` frame in the SSE stream to avoid an extra round-trip.
