## 1. Project Scaffold

- [ ] 1.1 Bootstrap `dialact-web/` with `npx create-next-app@latest --typescript --tailwind --app --src-dir --import-alias "@/*"` at `/Users/gavin/Documents/Projects/dialact/dialact-web`
- [ ] 1.2 Install dependencies: `@supabase/supabase-js @supabase/ssr`, `shadcn/ui` init, `lucide-react`, `clsx`, `tailwind-merge`
- [ ] 1.3 Add shadcn/ui components: `button`, `card`, `input`, `label`, `textarea`, `badge`, `dialog`, `dropdown-menu`, `separator`, `toast`
- [ ] 1.4 Create `.env.local.example` documenting all required env vars: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `VOICE_AGENT_URL`, `VOICE_AGENT_API_KEY`
- [ ] 1.5 Set up `src/lib/supabase/client.ts` (browser client with `createBrowserClient`) and `src/lib/supabase/server.ts` (server client with `createServerClient` using service role key)

## 2. Database Schema

- [ ] 2.1 Write Supabase migration SQL for `goal_templates` table with RLS policy (users own their templates)
- [ ] 2.2 Write Supabase migration SQL for `calls` table with RLS policy (users own their calls); include all fields from design: `voice_agent_id`, `transcript jsonb`, `summary`, `status`, `duration_secs`
- [ ] 2.3 Add `updated_at` trigger function to `goal_templates` that auto-updates on row modification
- [ ] 2.4 Create `src/lib/types.ts` with TypeScript types mirroring both tables (`GoalTemplate`, `Call`, `TranscriptTurn`, `CallStatus`)

## 3. Auth

- [ ] 3.1 Create `src/app/(auth)/login/page.tsx` — sign-in form (email + password) + Google OAuth button using Supabase Auth UI or custom form
- [ ] 3.2 Create `src/app/(auth)/signup/page.tsx` — sign-up form with email + password
- [ ] 3.3 Create `src/app/(auth)/callback/route.ts` — Supabase OAuth redirect handler (`exchangeCodeForSession`)
- [ ] 3.4 Create `src/app/(app)/layout.tsx` — auth guard that redirects unauthenticated users to `/login`; renders top navigation with user email + sign-out button
- [ ] 3.5 Add `middleware.ts` at project root using `@supabase/ssr` to refresh sessions on every request

## 4. Voice-Agent API Bridge

- [ ] 4.1 Create `src/lib/voice-agent.ts` with typed server-side helper functions: `launchCall(phone, goal, ivrMode)`, `hangupCall(callId)`, `takeoverCall(callId)`, `handbackCall(callId)`, `summarizeCall(transcript)` — all attach `X-API-Key` header from `VOICE_AGENT_API_KEY`
- [ ] 4.2 Create `src/app/api/calls/launch/route.ts` — POST handler: validates input, calls voice-agent `POST /dashboard/call`, inserts Supabase `calls` row with `status: active`, returns `{dbId, callId?}`
- [ ] 4.3 Create `src/app/api/calls/[id]/stream/route.ts` — WebSocket upgrade handler (Edge runtime): proxies browser WS ↔ voice-agent `/dashboard/ws`; on each transcript event appends to Supabase `calls.transcript`; on `call_ended` fetches summary, updates row to `completed`
- [ ] 4.4 Create `src/app/api/calls/[id]/hangup/route.ts` — POST: forwards to voice-agent hangup endpoint
- [ ] 4.5 Create `src/app/api/calls/[id]/takeover/route.ts` — POST: forwards to voice-agent takeover endpoint
- [ ] 4.6 Add error handling in all API routes: return typed `{error: string}` JSON with appropriate HTTP status on failure

## 5. Goal Templates

- [ ] 5.1 Create `src/app/(app)/templates/page.tsx` — Server Component; fetches user's templates from Supabase; renders list of `TemplateCard` components with edit/delete actions
- [ ] 5.2 Create `src/components/TemplateCard.tsx` — card showing template name, goal preview (truncated to 80 chars), IVR badge; action menu with Edit and Delete
- [ ] 5.3 Create `src/app/(app)/templates/new/page.tsx` — form (name, goal textarea, IVR toggle); on submit inserts to Supabase and redirects to `/templates`
- [ ] 5.4 Create `src/app/(app)/templates/[id]/edit/page.tsx` — pre-filled edit form; on submit updates Supabase row; includes Delete button with confirmation dialog
- [ ] 5.5 Add server actions in `src/app/(app)/templates/actions.ts` for `createTemplate`, `updateTemplate`, `deleteTemplate` (use Supabase server client; revalidate `/templates` path)

## 6. Call Launcher

- [ ] 6.1 Create `src/app/(app)/calls/new/page.tsx` — Server Component that fetches templates; renders the `LaunchCallForm` client component
- [ ] 6.2 Create `src/components/LaunchCallForm.tsx` — Client Component: template selector (dropdown, or "custom goal" option that shows a textarea); phone number input with E.164 validation; Launch button; on submit POSTs to `/api/calls/launch` then navigates to `/calls/[dbId]`
- [ ] 6.3 Add phone number validation utility in `src/lib/utils.ts` — strips spaces/dashes, validates E.164 format, returns `{valid: boolean, formatted: string}`

## 7. Live Call View

- [ ] 7.1 Create `src/app/(app)/calls/[id]/page.tsx` — fetches initial call record from Supabase (to get `voice_agent_id`); renders `LiveCallView` client component with initial transcript
- [ ] 7.2 Create `src/components/LiveCallView.tsx` — Client Component: connects to `/api/calls/[id]/stream` WebSocket; maintains local transcript state; renders `TranscriptFeed`, `PhaseIndicator`, `CallTimer`; shows Hangup and Takeover buttons; on `call_ended` event navigates to `/calls/[id]/review`
- [ ] 7.3 Create `src/components/TranscriptFeed.tsx` — scrollable list of transcript turns; auto-scrolls to bottom on new turns; agent turns right-aligned, user turns left-aligned; shows timestamp relative to call start
- [ ] 7.4 Create `src/components/PhaseIndicator.tsx` — badge component cycling LISTENING (green) / RESPONDING (blue) / ON HOLD (amber) based on WebSocket phase events
- [ ] 7.5 Create `src/components/CallTimer.tsx` — Client Component showing MM:SS elapsed since call start; uses `useEffect` with `setInterval`
- [ ] 7.6 Handle WebSocket reconnect in `LiveCallView`: if connection drops unexpectedly, attempt reconnect up to 3 times with 2s backoff before showing "Connection lost" state

## 8. Call History & Dashboard

- [ ] 8.1 Create `src/app/(app)/page.tsx` (dashboard) — Server Component: queries Supabase for active calls (status=active) and last 20 completed calls; renders two sections with `CallCard` components
- [ ] 8.2 Create `src/components/CallCard.tsx` — shows phone, goal preview, status badge, duration, started_at; links to live view (active) or review page (completed)
- [ ] 8.3 Add `revalidate = 30` to dashboard page so Next.js re-fetches call list every 30s; active calls page also has a client-side refresh button

## 9. Post-Call Review

- [ ] 9.1 Create `src/app/(app)/calls/[id]/review/page.tsx` — Server Component: fetches completed call record; renders summary text, full transcript, call metadata (duration, phone, goal, template link if applicable)
- [ ] 9.2 Add export button (Client Component) in review page: copies transcript as plain text to clipboard, or downloads as `.txt` file
- [ ] 9.3 Handle not-yet-completed calls: if `status` is still `active` when user navigates to `/review`, show a "Call in progress — check back soon" state with a link to the live view

## 10. Stale Call Cleanup

- [ ] 10.1 Add a Supabase SQL function `mark_stale_calls()` that sets `status='failed'` for any `calls` row with `status='active'` and `started_at < now() - interval '2 hours'`
- [ ] 10.2 Register `mark_stale_calls()` as a pg_cron job running every hour (document in migration file; pg_cron must be enabled in Supabase project)
- [ ] 10.3 On dashboard page load (server component), call `mark_stale_calls()` via RPC as a defensive fallback in case pg_cron is not enabled
