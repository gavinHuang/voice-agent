## Context

The platform currently runs as a single-tenant process: one Twilio account, one global call registry, one `/twiml` webhook, and goals delivered via env var or query param. Extending it to multi-tenant and multi-pattern use requires changes at the dispatch layer (web.py), the call lifecycle layer (call.py), and the infrastructure layer (new tenant.py). The state-machine core (`step()` in call.py) is pure and must not change.

Three patterns need to coexist:

1. **Standard inbound**: Twilio → `/twiml` → `/ws` → single-tenant agent (existing)
2. **Goal-directed outbound**: operator POSTs full `CallContext` → agent dials out with that goal
3. **Agent-to-agent**: one agent instance initiates a call to another agent instance, both under the same tenant

The constraint that there is only one Twilio webhook endpoint makes tenant disambiguation a routing concern: the system must identify the tenant from the Twilio request itself before doing anything else.

## Goals / Non-Goals

**Goals:**
- Strict tenant isolation: call state, goals, traces, and credentials never cross tenant boundaries
- One Twilio webhook endpoint handles all tenants
- Full `CallContext` accepted via REST body on outbound calls
- Agent-to-agent calls working locally (LocalPhone) and in prod (second Twilio leg)
- Existing single-tenant usage continues to work unchanged (default tenant)
- All existing tests pass; new benchmark scenario exercises two-agent flow

**Non-Goals:**
- Database-backed tenant persistence (in-memory + YAML file is sufficient for now)
- Per-tenant billing, rate limiting, or quota enforcement
- UI for tenant management
- Cross-tenant call transfer
- Changing the STT/TTS/LLM internals

## Decisions

### D1: Tenant ID resolution from Twilio request

**Decision**: Resolve `tenant_id` from the `AccountSid` field in the Twilio request body (both `/twiml` POST and `/ws` first message). Fall back to the `To` number when a single Twilio account is shared across tenants.

**Rationale**: `AccountSid` is always present, unforgeable (validated by Twilio signature), and unique per Twilio account. When running multiple tenants under one Twilio account, the `To` number disambiguates them. This requires no changes to Twilio configuration.

**Alternatives considered**:
- Custom `X-Tenant-ID` header: would require Twilio webhook config changes per tenant; fragile.
- Separate `/twiml/{tenant_id}` paths: forces separate webhook URLs per tenant; violates the single-endpoint constraint.
- JWT in `StatusCallback` URL: clever but adds latency and a second HTTP round-trip.

### D2: TenantStore protocol + InMemoryTenantStore default

**Decision**: Define `TenantStore` as a Python Protocol with `get(tenant_id) -> TenantConfig | None`. Ship `InMemoryTenantStore` (populated at startup from a YAML file) as the default. The FastAPI app accepts a `TenantStore` at construction time.

**Rationale**: Protocol-based design means operators can swap in a DB-backed store without touching the rest of the code. The in-memory default works for all current deployment sizes. YAML file makes dev/test easy.

```python
# shuo/tenant.py
@dataclass
class TenantConfig:
    tenant_id: str
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_phone_number: str
    default_goal: str | None = None
    tts_provider: str | None = None   # overrides env var
    voice_id: str | None = None
    allowed_to_numbers: list[str] = field(default_factory=list)

class TenantStore(Protocol):
    def get(self, tenant_id: str) -> TenantConfig | None: ...

class InMemoryTenantStore:
    def __init__(self, tenants: dict[str, TenantConfig]): ...
    def get(self, tenant_id: str) -> TenantConfig | None: ...
```

**Alternatives considered**:
- SQLite-backed store: over-engineered for current scale; adds a dependency.
- Config inside env vars: doesn't scale to N tenants; no clean isolation.

### D3: Tenant ID threaded through call lifecycle via `run_call()` parameter

**Decision**: Add `tenant_id: str` as a keyword argument to `run_call()`. It defaults to `"default"` with a deprecation warning when omitted. The call registry, tracer, and event bus all accept and propagate `tenant_id`.

**Rationale**: Avoids thread-locals or context vars (which are implicit and hard to test). The pure state machine (`step()`) does not need the tenant ID — it only exists at the I/O boundary. Explicit threading is consistent with the existing callback pattern.

**Alternatives considered**:
- Python `contextvars.ContextVar`: implicit; hard to test and reason about in async code.
- Attach tenant to the `Phone` instance: Phone is a telephony abstraction; tenant is a business concern; mixing them violates separation of concerns.

### D4: Agent-to-agent via `AgentPhone` wrapping `LocalPhone.pair()`

**Decision**: Add `AgentPhone` class to `phone.py`. It exposes the `Phone` protocol but internally creates a `LocalPhone.pair()` (in dev) or dials a second Twilio leg (in prod) to another `run_call()` coroutine.

**Rationale**: `LocalPhone.pair()` already exists for testing; it creates two paired in-process phones. `AgentPhone` is just a named constructor pattern on top of that for production use. No new abstractions needed.

```python
class AgentPhone:
    @staticmethod
    async def pair(
        caller_goal: CallContext,
        callee_goal: CallContext,
        tenant_id: str = "default",
    ) -> tuple[Phone, Phone]:
        return LocalPhone.pair()
```

For Twilio-mediated agent-to-agent: `dial_out()` is called with the second agent's webhook URL as the `statusCallback`, and the second agent runs in the same process on a different endpoint path (or a second process). This is an advanced pattern documented but not required for MVP.

**Alternatives considered**:
- WebRTC bridging: overkill; adds a real-time media server dependency.
- SIP trunking: out of scope and requires carrier setup.

### D5: `CallContext` as Pydantic model, accepted as JSON body

**Decision**: Convert `CallContext` in `context.py` from a plain dataclass to a Pydantic `BaseModel`. The existing `build_system_prompt()` function stays unchanged. Add a `POST /call/{phone}` endpoint that accepts `CallContext` as JSON body. The old `GET /call/{phone}?goal=` query-param endpoint remains for backwards compatibility.

**Rationale**: Pydantic gives free JSON deserialization, validation, and OpenAPI schema generation. The migration is mechanical (dataclass → BaseModel). `build_system_prompt()` only reads fields, so it works with both.

## Risks / Trade-offs

- **Single-process multi-tenant memory isolation**: all tenants share the same heap. A tenant with a very high call volume can indirectly affect others through GIL contention or memory pressure. Mitigation: document the limitation; multi-process deployment (one process per tenant) is always possible by running separate instances.

- **YAML TenantStore loaded at startup**: hot-reloading tenant config is not supported in the first iteration. Mitigation: restart the server; documented as a known limitation.

- **`run_call()` BREAKING change**: callers must now pass `tenant_id`. Mitigation: default to `"default"` with a deprecation warning so existing integrations continue to work without immediate changes.

- **Two-agent local call race**: `LocalPhone.pair()` is synchronous; both `run_call()` coroutines must be started concurrently. If one crashes before connecting, the other hangs. Mitigation: add a short connection timeout (5s) to `LocalPhone`.

## Migration Plan

1. Add `shuo/tenant.py` with `TenantConfig`, `TenantStore`, `InMemoryTenantStore`, `YamlTenantStore`.
2. Modify `shuo/call.py`: add `tenant_id` param to `run_call()`, thread it to registry and tracer.
3. Modify `shuo/web.py`: add tenant resolution in `/twiml`; update `run_call()` call sites; add `POST /call/{phone}` body endpoint.
4. Modify `monitor/registry.py`: add `tenant_id` to `ActiveCall`; filter queries by tenant.
5. Modify `shuo/phone.py`: add `AgentPhone`; `dial_out()` accepts optional `TenantConfig`.
6. Modify `shuo/context.py`: dataclass → Pydantic BaseModel.
7. Update `.env.example` and `docs/` with multi-tenant YAML format.
8. Add `simulator/flows/two_agent.yaml` + benchmark scenario.
9. Run full test suite; fix any failures.

Rollback: all changes are additive or backwards-compatible by default. Revert any commit in isolation without breaking existing single-tenant deployments.

## Open Questions

- Should `YamlTenantStore` watch the file for changes (inotify) or require restart? Starting with restart-only is simpler.
- For Twilio-mediated agent-to-agent in prod, should both agents share the same FastAPI process (different path) or be separate processes? Leave as a documented pattern; don't enforce in code yet.
