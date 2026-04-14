## Why

The platform runs well as a single-tenant, single-purpose agent, but three real use cases are blocked: (a) operators want to run isolated tenants through one Twilio number/webhook without cross-contaminating call state or credentials; (b) advanced workflows need agent-to-agent calls (e.g., a scheduling agent calling a confirmation agent); (c) the call-dispatch layer is too tightly coupled to FastAPI globals, making it fragile to extend. Addressing all three now keeps the architecture from forking as demand grows.

## What Changes

- **Tenant context on every call**: every call carries a `tenant_id` derived from the incoming Twilio request (e.g., `AccountSid`, custom param, or `To` number); the call registry, tracer, and goal store all key on `(tenant_id, call_id)` instead of `call_id` alone.
- **Tenant config store**: a pluggable `TenantStore` (in-memory default, swappable to DB/KV) maps `tenant_id` → per-tenant credentials, default goal, TTS/STT settings, and allowed phone numbers.
- **Routing table in `/twiml`**: before returning TwiML, the endpoint resolves the tenant and selects the right agent config; unknown tenants are rejected with a logged error and a polite TwiML hang-up.
- **Agent-to-agent call pattern**: introduce `AgentPhone`, a thin adapter that lets one `run_call()` instance dial another via `LocalPhone.pair()` (dev) or a second Twilio call with a well-known webhook (prod), scoped to the same `tenant_id`.
- **`CallContext` promoted to first-class API surface**: goals, constraints, and success criteria accepted via REST body (not just query param or env var), allowing full programmatic initiation of goal-directed calls.
- **Benchmark and config audit**: verify simulator tests, env-var docs, and benchmark scenarios all work correctly after the above changes; add a two-agent local benchmark scenario.
- **BREAKING**: `run_call()` signature gains a required `tenant_id` parameter; callers that pass `None` get a `"default"` tenant and a deprecation warning.

## Capabilities

### New Capabilities

- `tenant-routing`: Resolve tenant from inbound Twilio request; gate all call lifecycle operations (registry, tracer, goals, credentials) behind `tenant_id`; reject unrecognised tenants.
- `tenant-store`: Pluggable store (`TenantStore` protocol) for per-tenant config (credentials, default goal, TTS/STT overrides); ships with `InMemoryTenantStore` and a YAML-file loader for dev/test.
- `agent-to-agent-call`: `AgentPhone` adapter + `LocalPhone.pair()` wiring so one agent instance can initiate a call to another agent instance, with matching tenant scope and goal passing.
- `goal-directed-call-api`: Full `CallContext` accepted as JSON body on `POST /call/{phone}`; outbound calls programmable without CLI or env vars.

### Modified Capabilities

- `call-context`: Promote from internal dataclass to validated REST body schema; keep backwards-compat env-var fallback.

## Impact

- **`shuo/web.py`**: `/twiml` gains tenant resolution; `/call/{phone}` accepts JSON body; `run_call()` wiring passes `tenant_id`.
- **`shuo/call.py`**: `run_call()` signature adds `tenant_id`; no logic changes to the pure state machine.
- **`shuo/phone.py`**: Add `AgentPhone` class; `dial_out()` accepts optional per-tenant credentials.
- **`shuo/context.py`**: `CallContext` gains Pydantic model for REST deserialization.
- **`monitor/registry.py`**: `ActiveCall` gets `tenant_id` field; registry queries filter by tenant.
- **`monitor/bus.py`**: Events tagged with `tenant_id`; dashboard filters by tenant.
- **New `shuo/tenant.py`**: `TenantConfig`, `TenantStore` protocol, `InMemoryTenantStore`, `YamlTenantStore`.
- **`.env.example` / docs**: Document multi-tenant YAML config format.
- **`simulator/` + `eval/`**: Add two-agent scenario; verify existing benchmark still passes.
- **No changes** to STT (`speech.py`), TTS (`voice*.py`), or LLM (`language.py`) internals.
