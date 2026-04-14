## ADDED Requirements

### Requirement: Tenant resolution from inbound Twilio request
The system SHALL resolve a `tenant_id` from every inbound Twilio request before any call lifecycle operation begins. Resolution priority: (1) `AccountSid` field in the Twilio request body when each tenant uses a distinct Twilio account; (2) `To` phone number when multiple tenants share one Twilio account. If no tenant is matched, the system SHALL reject the call.

#### Scenario: Tenant resolved by AccountSid
- **WHEN** a Twilio webhook POST arrives with `AccountSid` matching a registered tenant
- **THEN** the system assigns that tenant's `tenant_id` to the call and proceeds with call setup

#### Scenario: Tenant resolved by To number
- **WHEN** a Twilio webhook POST arrives and `AccountSid` matches no registered tenant but `To` matches a configured phone number for a tenant
- **THEN** the system assigns that tenant's `tenant_id` to the call and proceeds with call setup

#### Scenario: Unknown tenant rejected
- **WHEN** a Twilio webhook POST arrives and neither `AccountSid` nor `To` matches any registered tenant
- **THEN** the system returns a TwiML `<Say>` hang-up response and logs a warning with the unrecognised `AccountSid` and `To` values; no call is created in the registry

### Requirement: Single `/twiml` endpoint handles all tenants
The system SHALL use a single `/twiml` webhook URL for all tenants. Tenant disambiguation MUST happen entirely within the request handler using request body fields — no per-tenant URL paths are required.

#### Scenario: Two tenants share one webhook URL
- **WHEN** two tenants are registered with different `AccountSid` values and both point their Twilio webhooks at the same `/twiml` URL
- **THEN** each inbound call is assigned the correct tenant and their call states remain strictly isolated

### Requirement: Tenant ID propagated through call lifecycle
Every call object, registry entry, trace file, and event bus message SHALL carry the `tenant_id` of the originating tenant. Queries against the registry or event bus MUST filter by `tenant_id` so that one tenant cannot observe another's calls.

#### Scenario: Registry query isolation
- **WHEN** a dashboard request fetches active calls for tenant A
- **THEN** only calls belonging to tenant A are returned; calls from tenant B are not visible

#### Scenario: Trace file isolation
- **WHEN** a call for tenant A completes and the trace is written
- **THEN** the trace file path includes the `tenant_id` (e.g., `/tmp/shuo/{tenant_id}/{call_id}.json`) so traces do not collide between tenants
