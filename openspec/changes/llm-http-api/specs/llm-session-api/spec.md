## ADDED Requirements

### Requirement: Create LLM session
The system SHALL expose `POST /llm/sessions` that accepts a `CallContext` JSON body, creates a `LanguageModel` instance configured with that context, and returns a unique `session_id`.

#### Scenario: Successful session creation
- **WHEN** a client POSTs a valid `CallContext` JSON to `/llm/sessions`
- **THEN** the server returns HTTP 201 with `{"session_id": "<uuid4>"}` and stores the session in memory

#### Scenario: Invalid body
- **WHEN** a client POSTs a body that fails `CallContext` validation
- **THEN** the server returns HTTP 422 with validation error details

### Requirement: Blocking generate
The system SHALL expose `POST /llm/sessions/{session_id}/generate` that accepts `{"message": "<text>"}`, runs a full LLM turn (blocking until complete), appends the turn to history, and returns a `TurnResult` JSON object.

#### Scenario: Successful generate
- **WHEN** a client POSTs `{"message": "Hello"}` to `/llm/sessions/{id}/generate`
- **THEN** the server returns HTTP 200 with `{"text": "...", "dtmf_digits": null, "hangup": false, "has_speech": true}` after the full LLM response is available

#### Scenario: Unknown session
- **WHEN** a client POSTs to `/llm/sessions/{unknown_id}/generate`
- **THEN** the server returns HTTP 404

#### Scenario: Concurrent generate blocked
- **WHEN** two generate requests arrive for the same session simultaneously
- **THEN** the second request waits until the first completes (per-session lock), then executes

### Requirement: SSE token stream
The system SHALL expose `POST /llm/sessions/{session_id}/stream` that accepts `{"message": "<text>"}` and responds with an `text/event-stream` SSE response. It SHALL emit one `data: {"type":"token","text":"<token>"}` event per speech token and a final `data: {"type":"done","text":"...","dtmf_digits":...,"hangup":...,"has_speech":...}` event when the turn completes.

#### Scenario: Streaming tokens
- **WHEN** a client POSTs to `/llm/sessions/{id}/stream`
- **THEN** the response has `Content-Type: text/event-stream` and the client receives token events followed by a done event

#### Scenario: Empty response (hangup signal only)
- **WHEN** the LLM produces no speech tokens (e.g., immediate hangup)
- **THEN** the stream emits zero token events and one `done` event with `"has_speech": false, "hangup": true`

#### Scenario: Stream for unknown session
- **WHEN** a client POSTs to `/llm/sessions/{unknown_id}/stream`
- **THEN** the server returns HTTP 404 (before opening the SSE stream)

### Requirement: Delete session
The system SHALL expose `DELETE /llm/sessions/{session_id}` that removes the session from memory.

#### Scenario: Successful deletion
- **WHEN** a client sends `DELETE /llm/sessions/{id}`
- **THEN** the server returns HTTP 204 and the session is no longer accessible

#### Scenario: Delete unknown session
- **WHEN** a client sends `DELETE /llm/sessions/{unknown_id}`
- **THEN** the server returns HTTP 404

### Requirement: Session idle expiry
The system SHALL automatically remove sessions that have been idle (no generate or stream request) for longer than the configured `LLM_SESSION_TTL_MINUTES` (default: 30).

#### Scenario: Idle session expired
- **WHEN** a session has received no requests for longer than `LLM_SESSION_TTL_MINUTES`
- **THEN** any subsequent request to that session returns HTTP 404 as if the session never existed

### Requirement: LLM client in dialact-eval
`dialact-eval` SHALL replace `EvalLanguageModel` with `LLMClient`, a thin async HTTP client that exposes `generate(message) -> TurnResult`, `stream_generate(message, on_token) -> TurnResult`, and `token_stream(message) -> AsyncIterator[str]` methods backed by voice-agent's `/llm` endpoints.

#### Scenario: generate delegates to voice-agent
- **WHEN** `LLMClient.generate("Hello")` is called
- **THEN** it POSTs to `POST /llm/sessions/{id}/generate` and returns a `TurnResult` matching the response body

#### Scenario: stream_generate delivers tokens via callback
- **WHEN** `LLMClient.stream_generate("Hello", on_token=cb)` is called
- **THEN** `cb` is invoked once per `token` SSE event and a `TurnResult` is returned after the `done` event

#### Scenario: token_stream yields tokens as async iterator
- **WHEN** `async for token in LLMClient.token_stream("Hello")` is called
- **THEN** each speech token from the SSE stream is yielded in order

#### Scenario: session lifecycle managed by LLMClient
- **WHEN** `LLMClient` is instantiated with a `CallContext` and `voice_agent_url`
- **THEN** it calls `POST /llm/sessions` to obtain a `session_id` on first use (lazy init) and `DELETE /llm/sessions/{id}` on `aclose()`
