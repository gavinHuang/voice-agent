## 1. voice-agent: LLM session router

- [x] 1.1 Create `shuo/llm_api.py`: define `SessionStore` (dict of `session_id → {model: LanguageModel, last_used: datetime, lock: asyncio.Lock}`) and a background TTL cleanup task
- [x] 1.2 Implement `POST /llm/sessions` endpoint: validate `CallContext` body, instantiate `LanguageModel`, store in `SessionStore`, return `{"session_id": "<uuid4>"}` (HTTP 201)
- [x] 1.3 Implement `POST /llm/sessions/{session_id}/generate` endpoint: acquire per-session lock, call `LanguageModel` accumulating all tokens, return `TurnResult` JSON (HTTP 200); return 404 if session unknown
- [x] 1.4 Implement `POST /llm/sessions/{session_id}/stream` endpoint: acquire per-session lock, return `StreamingResponse` with `text/event-stream`; emit `{"type":"token","text":"<t>"}` per speech token, then `{"type":"done",...TurnResult}` at completion; return 404 if session unknown
- [x] 1.5 Implement `DELETE /llm/sessions/{session_id}` endpoint: remove session from store, return 204; return 404 if unknown
- [x] 1.6 Mount `llm_router` in `shuo/web.py` with `app.include_router(llm_router, prefix="/llm")`

## 2. voice-agent: session idle expiry

- [x] 2.1 On each `generate` or `stream` request, update `last_used` timestamp for the session
- [x] 2.2 Register a FastAPI `lifespan` background task (or `asyncio` task created at startup) that sweeps `SessionStore` every minute and deletes sessions where `now - last_used > LLM_SESSION_TTL_MINUTES` (default: 30, from env var)

## 3. dialact-eval: LLMClient HTTP wrapper

- [x] 3.1 Rewrite `dialact-eval/core/language.py`: replace `EvalLanguageModel` with `LLMClient`; constructor accepts `context: CallContext`, `base_url: str` (default from `VOICE_AGENT_URL` env var); keep `TurnResult` dataclass in this file
- [x] 3.2 Implement lazy `_ensure_session()` on `LLMClient`: calls `POST /llm/sessions` with `context.model_dump()` on first use; stores `_session_id`
- [x] 3.3 Implement `async def generate(self, message: str) -> TurnResult`: calls `POST /llm/sessions/{id}/generate`, maps response JSON to `TurnResult`
- [x] 3.4 Implement `async def stream_generate(self, message: str, on_token: Callable[[str], None]) -> TurnResult`: calls `POST /llm/sessions/{id}/stream`, iterates SSE lines, invokes `on_token` per token event, returns `TurnResult` from done event
- [x] 3.5 Implement `async def token_stream(self, message: str) -> AsyncIterator[str]`: same SSE parsing as `stream_generate` but yields tokens instead of calling a callback
- [x] 3.6 Implement `async def aclose(self)`: calls `DELETE /llm/sessions/{id}` if session was initialised; close `httpx.AsyncClient`
- [x] 3.7 Add `__aenter__` / `__aexit__` to `LLMClient` so callers can use `async with`

## 4. dialact-eval: dependency and import updates

- [x] 4.1 Add `httpx[http2]>=0.27` to `dialact-eval/pyproject.toml` dependencies
- [x] 4.2 Add `VOICE_AGENT_URL` env var documentation to `dialact-eval` README or `.env.example`
- [x] 4.3 Update `dialact-eval/eval/runner.py`: replace `EvalLanguageModel(ctx)` instantiation with `LLMClient(ctx)` (constructor signature is the same; add `async with` or explicit `aclose()` after scenario)
- [x] 4.4 Update `dialact-eval` chat UI against local voice-agent; confirm streaming tokens appear in browser
- [x] 4.5 Remove now-unused direct pydantic-ai imports from `dialact-eval/core/language.py` (groq agent setup, tool definitions, `_TurnCtx`, etc.)

## 5. Verification

- [ ] 5.1 Start voice-agent locally; confirm `POST /llm/sessions` returns 201 with a UUID
- [ ] 5.2 Run `POST /llm/sessions/{id}/generate` with a test message; confirm valid `TurnResult` JSON response
- [ ] 5.3 Run `POST /llm/sessions/{id}/stream` with curl or httpx; confirm SSE token events arrive followed by a `done` event
- [ ] 5.4 Start `dialact-eval` chat UI against local voice-agent; confirm streaming tokens appear in browser
- [ ] 5.5 Run one eval scenario via `dialact-eval` CLI; confirm pass/fail report is produced correctly
- [ ] 5.6 Confirm idle session expiry: create a session, wait past TTL, verify subsequent request returns 404

