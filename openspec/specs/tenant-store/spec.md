## ADDED Requirements

### Requirement: TenantStore protocol
The system SHALL define a `TenantStore` protocol in `shuo/tenant.py` with a single method `get(tenant_id: str) -> TenantConfig | None`. Any object implementing this method SHALL be accepted as a tenant store. The FastAPI application SHALL accept a `TenantStore` instance at construction time.

#### Scenario: Custom store injected at startup
- **WHEN** the application is constructed with a custom `TenantStore` implementation
- **THEN** all tenant lookups during request handling use that implementation without any code changes to the request handlers

### Requirement: TenantConfig holds per-tenant settings
`TenantConfig` SHALL be a dataclass with fields: `tenant_id`, `twilio_account_sid`, `twilio_auth_token`, `twilio_phone_number`, and optional overrides `default_goal`, `tts_provider`, `voice_id`, `allowed_to_numbers`. Per-tenant settings MUST override the corresponding environment variables for any call belonging to that tenant.

#### Scenario: Per-tenant TTS provider
- **WHEN** a tenant's `TenantConfig` specifies `tts_provider = "kokoro"` and the global env var is `TTS_PROVIDER=elevenlabs`
- **THEN** calls for that tenant use the Kokoro TTS provider

#### Scenario: Default tenant falls back to env vars
- **WHEN** the `"default"` tenant is used and its `TenantConfig` fields are `None`
- **THEN** the system falls back to the corresponding environment variable values, preserving existing behaviour

### Requirement: InMemoryTenantStore
The system SHALL ship `InMemoryTenantStore` as the default implementation. It SHALL be initialised from a dict of `{tenant_id: TenantConfig}` at startup and be immutable thereafter.

#### Scenario: Startup with multiple tenants
- **WHEN** `InMemoryTenantStore` is created with two `TenantConfig` entries
- **THEN** `get()` returns the correct config for each `tenant_id` and `None` for unknown IDs

### Requirement: YamlTenantStore
The system SHALL ship `YamlTenantStore` that loads tenant configs from a YAML file at startup. The YAML format SHALL be a list of tenant objects matching the `TenantConfig` fields. The store SHALL raise a clear error if the file is missing or malformed.

#### Scenario: Load from YAML file
- **WHEN** `YamlTenantStore` is pointed at a valid YAML file with two tenant entries
- **THEN** both tenants are available via `get()` after construction

#### Scenario: Missing file raises error at startup
- **WHEN** `YamlTenantStore` is constructed with a path to a non-existent file
- **THEN** a `FileNotFoundError` with a descriptive message is raised immediately at startup, not at the first request

### Requirement: Single-tenant backward compatibility
When no `TenantStore` is provided, the system SHALL construct a default `InMemoryTenantStore` with one `"default"` tenant whose settings are read entirely from environment variables. This MUST make existing single-tenant deployments work with zero configuration changes.

#### Scenario: No tenant store configured
- **WHEN** the application starts without a `TENANTS_YAML` env var and without an explicit `TenantStore`
- **THEN** all inbound calls are assigned `tenant_id = "default"` and the system behaves identically to the pre-multi-tenant version
