## ADDED Requirements

### Requirement: POST /call/{phone} accepts full CallContext as JSON body
The system SHALL expose `POST /call/{phone_number}` that accepts a JSON body matching the `CallContext` schema. The endpoint SHALL initiate an outbound call using the provided context as the agent's goal and system prompt. The response SHALL include the `call_sid` and `call_id`.

#### Scenario: Outbound call with full context
- **WHEN** a client POSTs `{"goal": "Book a dental appointment", "agent_name": "Sam", "constraints": ["be polite"]}` to `POST /call/+61400000000`
- **THEN** an outbound call is placed, the agent uses the provided context, and the response body contains `{"call_sid": "...", "call_id": "..."}`

#### Scenario: Missing goal returns 422
- **WHEN** a client POSTs an empty body `{}` to `POST /call/+61400000000`
- **THEN** the server returns HTTP 422 with a validation error indicating `goal` is required

### Requirement: GET /call/{phone}?goal= remains for backward compatibility
The existing `GET /call/{phone_number}?goal=<text>` endpoint SHALL continue to work unchanged. It MUST NOT be removed or modified. Both `GET` and `POST` variants SHALL coexist on the same route.

#### Scenario: Legacy GET call still works
- **WHEN** a client sends `GET /call/+61400000000?goal=Call+to+confirm+appointment`
- **THEN** an outbound call is placed with that goal, identical to pre-change behaviour

### Requirement: Tenant-scoped outbound calls via API
When the server is in multi-tenant mode, `POST /call/{phone}` SHALL accept an optional `tenant_id` field in the request body. If omitted, the call is placed under the `"default"` tenant. The outbound call SHALL use the matching tenant's Twilio credentials.

#### Scenario: Outbound call placed under named tenant
- **WHEN** `POST /call/+61400000000` body includes `"tenant_id": "acme"`
- **THEN** the call is placed using `acme`'s Twilio credentials and the call appears in the registry under `tenant_id = "acme"`

#### Scenario: Invalid tenant returns 404
- **WHEN** `POST /call/+61400000000` body includes `"tenant_id": "unknown-tenant"`
- **THEN** the server returns HTTP 404 with `{"error": "tenant not found: unknown-tenant"}`
