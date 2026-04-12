## Why

The `voice-agent` backend is fully capable of making AI-powered phone calls, but it has no user-facing surface. The only interface is an internal supervisor dashboard (`monitor/app.html`) — a single static HTML file with no auth, no persistence, and no way for a non-technical user to define what they want the agent to accomplish. Call history disappears on restart. Goals are typed as raw free-text strings each time. There is no concept of reusable templates, user accounts, or call review.

To turn `voice-agent` into a usable product, we need a purpose-built web frontend that: (1) lets users define and save call goals as templates, (2) launches calls with one click, (3) shows live call status with a transcript feed, and (4) stores call history with LLM-generated summaries for review.

## What Changes

- **New repo**: `dialact-web/` — a Next.js 14 App Router project, sibling to `voice-agent/`, with no shared code at deploy time.
- **New**: Goal template CRUD — users create named templates (e.g. "Cancel gym subscription") with a goal string and optional IVR flag; stored in Supabase.
- **New**: Call launcher — select a template (or enter ad-hoc goal), enter a phone number, launch via `POST /dashboard/call` on the voice-agent backend.
- **New**: Live call view — real-time transcript feed via WebSocket `/dashboard/ws`, phase badge (LISTENING / RESPONDING / ON HOLD), elapsed timer, hangup/takeover controls.
- **New**: Call history with persistence — every call written to Supabase `calls` table; post-call LLM summary fetched from `POST /dashboard/summarize`; transcript stored.
- **New**: Auth — Supabase Auth (email + OAuth); each user owns their templates and calls.
- **New**: API bridge — Next.js server-side API routes proxy all voice-agent requests, attaching `X-API-Key` and translating WebSocket connections so credentials never reach the browser.

## Capabilities

### New Capabilities

- `goal-templates`: Full CRUD for named, reusable call goal templates with optional IVR mode flag and per-user ownership.
- `call-launcher`: One-click call initiation from a template or custom goal; validates phone number format before submitting.
- `live-call-view`: Real-time transcript streamed from voice-agent WebSocket; phase indicator; elapsed timer; hangup and takeover controls surfaced from monitor API.
- `call-history`: Persistent call log per user with phone number, goal, duration, outcome, LLM-generated summary, and full transcript.
- `call-review`: Post-call summary page with summary text, scrollable transcript, and JSON/text export.
- `auth`: Sign-up / sign-in (email+password, Google OAuth) via Supabase Auth; Row-Level Security enforces per-user data isolation.
- `api-bridge`: Server-side Next.js API routes that proxy voice-agent REST calls and upgrade browser WebSocket connections through a server-side tunnel.

### Modified Capabilities

- `voice-agent monitor API`: No code changes required. The web app is a new consumer of the existing `/dashboard/*` API surface. The `DASHBOARD_API_KEY` env var gains practical importance as the bridge authenticates all requests with it.

## Impact

- **New project**: `dialact-web/` — Next.js 14, TypeScript, Tailwind CSS, shadcn/ui, Supabase JS client.
- **New Supabase schema**: `goal_templates` and `calls` tables with RLS policies.
- **No changes to `voice-agent/`** — the backend API is consumed as-is.
- **New env vars** (dialact-web only): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `VOICE_AGENT_URL`, `VOICE_AGENT_API_KEY`.
- **Deployment**: Vercel (dialact-web) + existing Railway deploy (voice-agent); both independent.
