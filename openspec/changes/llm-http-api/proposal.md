## Why

`dialact-eval` currently duplicates all LLM wiring from `shuo/language.py` in its own `EvalLanguageModel` class, requiring both projects to stay in sync manually. Making voice-agent expose its LLM layer over HTTP lets dialact-eval consume the same model logic without code duplication or a direct package dependency.

## What Changes

- **voice-agent**: Add a `/llm` HTTP router with stateful session management — create session, generate (non-streaming), and stream (SSE) endpoints that wrap `LanguageModel` internally.
- **dialact-eval**: Replace `EvalLanguageModel` with a thin HTTP client (`LLMClient`) that implements the same `generate` / `stream_generate` / `token_stream` interface, pointing at voice-agent's `/llm` endpoints.
- **dialact-eval**: Remove the `shuo` local package dependency from `pyproject.toml`; replace direct `shuo.*` imports in `core/` with either local equivalents or data models mirrored from the HTTP contract.

## Capabilities

### New Capabilities

- `llm-session-api`: HTTP API on voice-agent for stateful LLM sessions — create, generate (blocking), stream (SSE), and delete session endpoints. Sessions hold `LanguageModel` instance + history in memory, keyed by session ID.

### Modified Capabilities

<!-- none -->

## Impact

- **voice-agent** (`shuo/web.py`, new `shuo/llm_api.py`): new FastAPI router mounted at `/llm`.
- **dialact-eval** (`core/language.py`): rewritten as HTTP client; `EvalLanguageModel` → `LLMClient`.
- **dialact-eval** (`core/context.py`, `core/translation.py`): may lose direct `shuo.*` re-exports; `CallContext` schema must be kept compatible with what voice-agent expects.
- **dialact-eval** (`eval/runner.py`, `ui/app.py`): caller interface unchanged — same `generate()` / `stream_generate()` / `token_stream()` methods.
- **dialact-eval** (`pyproject.toml`): `shuo` dependency removed; `httpx` (async HTTP client) added.
