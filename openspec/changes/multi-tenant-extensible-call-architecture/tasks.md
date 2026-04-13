## 1. Tenant Infrastructure (shuo/tenant.py)

- [x] 1.1 Create `shuo/tenant.py` with `TenantConfig` dataclass (fields: tenant_id, twilio_account_sid, twilio_auth_token, twilio_phone_number, default_goal, tts_provider, voice_id, allowed_to_numbers)
- [x] 1.2 Define `TenantStore` Protocol with `get(tenant_id: str) -> TenantConfig | None`
- [x] 1.3 Implement `InMemoryTenantStore` initialised from a `dict[str, TenantConfig]`
- [x] 1.4 Implement `YamlTenantStore` that loads from a YAML file at construction time; raise `FileNotFoundError` with descriptive message if file missing or malformed
- [x] 1.5 Add `resolve_tenant(request_body: dict, store: TenantStore) -> TenantConfig | None` helper: try `AccountSid` first, then `To` number match across all configs
- [x] 1.6 Add unit tests for `InMemoryTenantStore`, `YamlTenantStore`, and `resolve_tenant`

## 2. CallContext → Pydantic BaseModel

- [x] 2.1 Convert `CallContext` in `shuo/context.py` from dataclass to `pydantic.BaseModel`; preserve all existing fields and defaults
- [x] 2.2 Verify `build_system_prompt(ctx)` works unchanged with Pydantic model
- [x] 2.3 Verify CLI YAML loading of `CallContext` still works
- [x] 2.4 Add round-trip JSON serialisation test for `CallContext`

## 3. Thread tenant_id through call lifecycle

- [x] 3.1 Add `tenant_id: str = "default"` keyword argument to `run_call()` in `shuo/call.py`; emit deprecation warning if caller passes `None`
- [x] 3.2 Add `tenant_id` field to `ActiveCall` dataclass in `monitor/registry.py`
- [x] 3.3 Update `CallRegistry.add()`, `get()`, and `list()` to accept and filter by `tenant_id`
- [x] 3.4 Update event bus messages in `monitor/bus.py` to include `tenant_id`
- [x] 3.5 Update trace file path in `shuo/tracer.py` to use `/tmp/shuo/{tenant_id}/{call_id}.json`
- [x] 3.6 Update all `run_call()` call sites in `shuo/web.py` to pass `tenant_id`

## 4. Tenant Routing in web.py

- [x] 4.1 Accept a `TenantStore` at FastAPI app construction in `shuo/web.py`; default to `InMemoryTenantStore` with a single `"default"` tenant from env vars
- [x] 4.2 In `/twiml` POST handler: call `resolve_tenant()` before any call setup; if no tenant found, return TwiML `<Say>` hang-up and log warning
- [x] 4.3 Pass resolved `TenantConfig` to `run_call()` and use per-tenant Twilio credentials in `dial_out()` when present
- [x] 4.4 Add `TENANTS_YAML` env var support: if set at startup, load `YamlTenantStore` from that path; document in `.env.example`
- [x] 4.5 Update `/ws` WebSocket handler to derive `tenant_id` from Twilio start message `accountSid` field

## 5. Goal-Directed Call API

- [x] 5.1 Add `POST /call/{phone_number}` endpoint accepting `CallContext` as JSON body (with optional `tenant_id` field)
- [x] 5.2 Return `{"call_sid": "...", "call_id": "..."}` on success; return HTTP 404 with error message if tenant not found
- [x] 5.3 Keep existing `GET /call/{phone_number}?goal=` endpoint unchanged for backward compatibility
- [x] 5.4 Add integration test for `POST /call/{phone}` with mock `dial_out()`

## 6. AgentPhone and Agent-to-Agent Calls

- [x] 6.1 Add `AgentPhone` class to `shuo/phone.py` with `pair(caller_goal, callee_goal, tenant_id) -> tuple[Phone, Phone]` static method wrapping `LocalPhone.pair()`
- [x] 6.2 Add 5-second connection timeout to `LocalPhone` paired instances; raise `TimeoutError` if remote side does not consume audio within the timeout
- [x] 6.3 Write unit test: two `run_call()` coroutines using `AgentPhone.pair()` exchange at least one message and complete cleanly

## 7. Benchmark and Simulator

- [x] 7.1 Create `simulator/flows/two_agent.yaml` with a simple two-turn agent-to-agent scenario
- [x] 7.2 Create `eval/scenarios/two_agent.yaml` benchmark dataset pointing at the two-agent flow
- [x] 7.3 Run `voice-agent bench --dataset eval/scenarios/two_agent.yaml` and confirm it completes without errors
- [x] 7.4 Run full test suite (`python -m pytest tests/ simulator/tests/ -v`) and confirm 133+ tests pass with no new failures

## 8. Documentation and Config

- [x] 8.1 Update `.env.example` with `TENANTS_YAML` variable and a comment explaining the multi-tenant YAML format
- [x] 8.2 Add `docs/multi-tenant.md` with YAML config example showing two tenants sharing one Twilio account, disambiguated by `To` number
- [x] 8.3 Add `docs/agent-to-agent.md` explaining `AgentPhone.pair()` usage and the local two-agent benchmark scenario
