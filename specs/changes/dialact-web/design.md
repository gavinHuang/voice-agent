## Context

`voice-agent` exposes a complete monitor API at `/dashboard/*`:
- `POST /dashboard/call` — initiates a call `{phone, goal, ivr_mode}`
- `WS /dashboard/ws` — streams call lifecycle events (transcript turns, phase changes, call start/end)
- `POST /dashboard/calls/{id}/hangup`, `/takeover`, `/handback`, `/dtmf` — call controls
- `POST /dashboard/summarize` — LLM-generated summary from `{transcript}`

The backend has no persistence (in-memory only), no auth, and no concept of call templates. The web app must add all three without modifying the backend.

The voice-agent backend is deployed at a known base URL with an optional `DASHBOARD_API_KEY`. All backend communication must happen server-side in Next.js API routes to keep that key out of the browser.

The WebSocket `/dashboard/ws` is a long-lived connection that emits JSON events until the call ends. The browser must receive these events in real time. Since the browser can't hold the API key, the Next.js server must act as a WebSocket proxy.

---

## Goals / Non-Goals

**Goals:**
- Users can sign in and manage their own templates and call history.
- A call can be launched from a template in ≤3 clicks.
- The live call view updates within 1s of a transcript event arriving at the voice-agent server.
- All calls are persisted to Supabase regardless of whether the browser tab stays open.
- Post-call summary is generated and stored automatically when a call ends.

**Non-Goals:**
- Real-time audio playback or monitoring in the browser (no media stream).
- Multi-user team workspaces or sharing (single-user scope per account).
- Modifying the voice-agent backend.
- Mobile-native app.
- Billing/metering (no cost tracking in this change).

---

## Decisions

### 1. Next.js App Router with server components

**Decision**: Use Next.js 14 App Router. Pages that show persisted data (history, template list) are React Server Components fetching from Supabase directly. Interactive pages (live call view) are Client Components.

**Alternative**: Pages Router or a separate SPA. Rejected — App Router's server/client split naturally maps to the read-heavy vs. interactive pages, and Supabase SSR helpers work natively with it.

### 2. Supabase for auth + persistence

**Decision**: Supabase Auth (email + Google OAuth) and Supabase Postgres for `goal_templates` and `calls` tables with Row-Level Security. The Supabase JS client runs in server components with the service role key; the anon key is used only in browser-side auth flows.

**Alternative**: NextAuth + PlanetScale / Neon. Rejected — Supabase provides auth, postgres, and RLS in one service with first-class Next.js SSR support; reduces infrastructure surface for a greenfield project.

**Alternative**: No auth (single-user, API key only). Rejected — multi-user is a core gap identified in the proposal.

### 3. WebSocket proxy via Next.js API route

**Decision**: A dedicated Next.js API route (`/api/calls/[id]/stream`) upgrades the incoming browser WebSocket connection, opens a server-side WebSocket to `VOICE_AGENT_URL/dashboard/ws?call_id=<id>`, and bidirectionally pipes messages. The `X-API-Key` header is injected on the server side.

**Why not Server-Sent Events**: The voice-agent emits bidirectional events (the frontend may want to send control signals later). WebSocket proxy preserves that option.

**Alternative**: Poll `/dashboard/calls` every second. Rejected — polling would introduce up to 1s lag and hammers the voice-agent server with unnecessary requests.

**Alternative**: Expose voice-agent directly to the browser and store the API key in `NEXT_PUBLIC_*`. Rejected — leaks credentials.

### 4. Call persistence strategy: write-on-events, not write-on-end

**Decision**: When a call is launched via `POST /api/calls/launch`, immediately insert a `calls` row in Supabase with `status: active`. The WebSocket proxy route updates the row as events arrive (appending transcript turns). When a `call_ended` event arrives, the proxy fetches a summary from `/dashboard/summarize`, writes it to the row, and sets `status: completed`.

**Why**: If the browser tab closes during a call, the server-side proxy is still running and will still complete the record. Waiting for the browser to signal "done" would leave orphaned `active` rows.

**Alternative**: Webhook from voice-agent to Next.js. Rejected — would require voice-agent code changes and a stable public URL for dialact-web.

### 5. shadcn/ui component library

**Decision**: Use shadcn/ui (Radix primitives + Tailwind) for all UI components. Copy components into `src/components/ui/` at init time; no runtime dependency.

**Alternative**: MUI, Chakra, or Mantine. Rejected — shadcn/ui produces minimal bundle output and the copy-in model means no upstream breakage; consistent with modern Next.js projects.

### 6. Database schema

```sql
-- goal_templates
create table goal_templates (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  name        text not null,
  goal        text not null,
  ivr_mode    boolean not null default false,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
alter table goal_templates enable row level security;
create policy "users own templates"
  on goal_templates for all using (auth.uid() = user_id);

-- calls
create table calls (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references auth.users(id) on delete cascade,
  template_id     uuid references goal_templates(id) on delete set null,
  voice_agent_id  text,              -- call_id from voice-agent registry
  phone           text not null,
  goal            text not null,
  ivr_mode        boolean not null default false,
  status          text not null default 'active',  -- active | completed | failed
  started_at      timestamptz not null default now(),
  ended_at        timestamptz,
  duration_secs   integer,
  transcript      jsonb default '[]',  -- [{role, text, ts}]
  summary         text,
  created_at      timestamptz not null default now()
);
alter table calls enable row level security;
create policy "users own calls"
  on calls for all using (auth.uid() = user_id);
```

### 7. Page and route structure

```
dialact-web/
├── src/
│   ├── app/
│   │   ├── (auth)/
│   │   │   ├── login/page.tsx           # Sign in
│   │   │   └── signup/page.tsx          # Sign up
│   │   ├── (app)/
│   │   │   ├── layout.tsx               # Auth guard + nav
│   │   │   ├── page.tsx                 # Dashboard: active calls + recent history
│   │   │   ├── templates/
│   │   │   │   ├── page.tsx             # Template list
│   │   │   │   ├── new/page.tsx         # Create template
│   │   │   │   └── [id]/edit/page.tsx   # Edit template
│   │   │   └── calls/
│   │   │       ├── new/page.tsx         # Launch call
│   │   │       ├── [id]/page.tsx        # Live call view
│   │   │       └── [id]/review/page.tsx # Post-call review
│   │   └── api/
│   │       ├── calls/
│   │       │   ├── launch/route.ts      # POST: initiate call via voice-agent
│   │       │   └── [id]/
│   │       │       ├── stream/route.ts  # WS proxy to voice-agent /dashboard/ws
│   │       │       ├── hangup/route.ts  # POST: forward hangup
│   │       │       └── takeover/route.ts # POST: forward takeover
│   │       └── templates/
│   │           └── route.ts             # GET/POST for templates (RSC fallback)
│   ├── components/
│   │   ├── ui/                          # shadcn/ui copies
│   │   ├── TranscriptFeed.tsx           # Scrolling live transcript
│   │   ├── PhaseIndicator.tsx           # LISTENING/RESPONDING/ON HOLD badge
│   │   ├── CallTimer.tsx                # Elapsed time counter
│   │   ├── TemplateCard.tsx             # Template list item
│   │   └── CallCard.tsx                 # Call history item
│   └── lib/
│       ├── supabase/
│       │   ├── client.ts                # Browser Supabase client
│       │   └── server.ts                # Server Supabase client (service role)
│       ├── voice-agent.ts               # Typed wrappers for voice-agent REST API
│       └── types.ts                     # Shared TS types
```

### 8. Live call view data flow

```
Browser (calls/[id]/page.tsx)
  └─ useWebSocket("/api/calls/[id]/stream")
       │
       ▼
Next.js API route (/api/calls/[id]/stream)
  ├─ Upgrades to WebSocket (node:http upgrade)
  ├─ Opens WS to VOICE_AGENT_URL/dashboard/ws (with X-API-Key)
  ├─ Pipes events to browser
  ├─ On each {type:"transcript"} event:
  │    └─ UPDATE calls SET transcript = transcript || [turn] WHERE id = $dbId
  └─ On {type:"call_ended"} event:
       ├─ POST VOICE_AGENT_URL/dashboard/summarize
       ├─ UPDATE calls SET status='completed', ended_at=now(), summary=...
       └─ Close both WebSockets
```

---

## Risks / Trade-offs

- **WebSocket proxy longevity** — Next.js serverless functions time out. On Vercel, the WS proxy route must use the Edge runtime or a long-polling fallback. Mitigation: deploy the proxy as an Edge function (`export const runtime = 'edge'`); edge functions support streaming WebSocket connections on Vercel.

- **Supabase RLS misconfiguration** — If policies are wrong, users could see each other's data. Mitigation: integration test with two distinct users verifying isolation before shipping.

- **voice-agent `call_id` vs Supabase `id`** — The voice-agent assigns its own `call_id` only after Twilio answers (async). The Supabase row is created before that. Mitigation: `launch/route.ts` waits for the `call_started` event on the WebSocket (or a short poll on `/dashboard/calls`) to back-fill `voice_agent_id` into the Supabase row.

- **Orphaned `active` calls** — If the voice-agent server restarts mid-call, no `call_ended` event fires. Mitigation: a nightly Supabase cron (pg_cron) or a `started_at < now() - interval '2 hours'` check on page load sets stale `active` rows to `failed`.
