# Multi-Tenant Configuration

The voice agent server supports multiple tenants sharing a single Twilio webhook endpoint. Each tenant has its own Twilio credentials, phone number, and agent defaults. Inbound calls are routed to the correct tenant automatically.

## How tenant routing works

When a call arrives at `/twiml`, the server resolves the tenant using two strategies in priority order:

1. **AccountSid match** — if tenants use different Twilio accounts, the `AccountSid` field in the webhook uniquely identifies the tenant.
2. **To number match** — if tenants share one Twilio account but use different phone numbers, the `To` field (destination number) disambiguates them.

If no tenant matches, the call is rejected with a TwiML `<Say>` message and a hang-up.

## Configuration

Set the `TENANTS_YAML` environment variable to a YAML file path:

```
TENANTS_YAML=/etc/voice-agent/tenants.yaml
```

### Two tenants sharing one Twilio account (different phone numbers)

```yaml
- tenant_id: acme
  twilio_account_sid: AC111aaaa...
  twilio_auth_token: secret1
  twilio_phone_number: "+15550001111"
  default_goal: "You are ACME customer support. Help the caller resolve their issue."

- tenant_id: globex
  twilio_account_sid: AC111aaaa...   # same Twilio account
  twilio_auth_token: secret1
  twilio_phone_number: "+15550002222"  # different number → disambiguated by To
  default_goal: "You are Globex sales. Qualify the lead and schedule a demo."
  tts_provider: kokoro
  voice_id: af_sky
```

### Two tenants with separate Twilio accounts

```yaml
- tenant_id: tenant-a
  twilio_account_sid: ACaaa...
  twilio_auth_token: token_a
  twilio_phone_number: "+15550001111"
  default_goal: "You are Tenant A support."

- tenant_id: tenant-b
  twilio_account_sid: ACbbb...   # different Twilio account → disambiguated by AccountSid
  twilio_auth_token: token_b
  twilio_phone_number: "+15550002222"
  default_goal: "You are Tenant B support."
```

## Tenant config fields

| Field | Required | Description |
|-------|----------|-------------|
| `tenant_id` | yes | Unique identifier used in traces, logs, and registry |
| `twilio_account_sid` | yes | Twilio Account SID for outbound calls from this tenant |
| `twilio_auth_token` | yes | Twilio Auth Token for this tenant |
| `twilio_phone_number` | yes | Caller ID for outbound calls; used for inbound disambiguation |
| `default_goal` | no | System prompt goal used when no per-call goal is provided |
| `tts_provider` | no | Override TTS provider (`elevenlabs`, `kokoro`, `fish`) |
| `voice_id` | no | Override TTS voice ID |
| `allowed_to_numbers` | no | Extra destination numbers that route to this tenant |

## Trace isolation

Each tenant's call traces are written to `/tmp/shuo/{tenant_id}/{call_id}.json`, keeping trace data segregated.

## Single-tenant (default)

If `TENANTS_YAML` is not set, a single `"default"` tenant is created from the standard env vars (`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`). No configuration file is needed.
