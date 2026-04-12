## Context

Currently, `voice-agent call <phone>` accepts a single `--goal` string. The agent receives this as-is and fills in everything else (its own name, role, caller context, constraints) via hallucination or hardcoded defaults buried in the system prompt template. There is no structured way to express "who am I calling?", "who am I?", or "what facts should I never make up?".

The goal of this change is to introduce a `CallContext` — a typed, declarative contract between the human operator and the agent — and a pre-call confirmation gate that ensures every required field is present before the first audio byte is sent.

## Goals / Non-Goals

**Goals:**
- Define a `CallContext` dataclass with typed, documented fields (required + optional)
- Provide a default agent persona (`Alex`) so new callers get sensible defaults without configuration
- Support convention-based persona loading from `identity.md` in the current project or `~/identity.md`
- Gate every outbound call behind a confirmation step that validates required fields and previews the assembled context
- Drive system prompt assembly entirely from `CallContext` — no ad-hoc string concatenation elsewhere
- Keep the confirmation UX in the CLI only; WebSocket / Twilio path is unchanged

**Non-Goals:**
- Inbound call context (no caller ID enrichment, no CRM lookup)
- Persistent call profiles or saved contexts
- GUI for context entry
- Changing the audio pipeline or latency budget

## Decisions

### D1: `CallContext` as a typed dataclass, not a free-form dict

**Decision:** Define `CallContext` in `shuo/context.py` as a Python `dataclass` with explicit field names, types, and defaults. Required fields have no default; optional fields have `None` or a sensible literal default.

**Rationale:** A dataclass gives us IDE completion, `--help` introspection, and a single authoritative list of fields. A dict lets anything slip through silently. The dataclass is also directly serializable to YAML/JSON for future persistence.

**Alternatives considered:**
- Pydantic model: adds a dependency and validation overhead we don't need here; dataclass is sufficient.
- Click `@option` for each field: bloats the CLI signature and couples schema to CLI layer; better to pass a context object.

### D2: Default persona is `Alex, professional assistant`

**Decision:** When `agent_name` and `agent_role` are not provided, the agent introduces itself as "Alex" and describes its role as "a professional assistant." Tone defaults to "friendly and concise."

**Rationale:** A named, role-defined persona eliminates the most common hallucination: the agent making up a name or affiliation. "Alex" is gender-neutral and broadly accepted. The defaults are intentionally generic — operators override them for branded use cases.

**Alternatives considered:**
- No default persona (force operator to always supply): creates friction for quick tests and demos.
- Randomized name: unpredictable behavior, harder to debug call recordings.

### D3: Pre-call confirmation is always interactive in CLI, skippable via `--yes`

**Decision:** The `voice-agent call` command prints the assembled `CallContext`, prompts for any missing required fields, then asks "Proceed? [y/N]". A `--yes` / `-y` flag skips the prompt for scripted/CI use.

**Rationale:** Operators need to see exactly what the agent will say about itself before the call starts. The `--yes` flag preserves automation compatibility.

**Alternatives considered:**
- Auto-proceed after N seconds: hides the confirmation from fast runners.
- Separate `voice-agent preview` command: extra step, easy to forget.

### D4: System prompt is assembled from `CallContext` by a single function in `context.py`

**Decision:** `build_system_prompt(ctx: CallContext) -> str` is the only place that constructs the agent's system prompt. `language.py` calls this function; it no longer contains inline prompt strings.

**Rationale:** Centralizing prompt assembly makes it testable in isolation and prevents drift between the context schema and what the agent actually knows about itself.

### D5: `--context` flag accepts a YAML file; individual fields can also override via flags

**Decision:** `voice-agent call <phone> --context ctx.yaml` loads a `CallContext` from a YAML file. Individual flags (`--goal`, `--agent-name`, `--agent-role`, `--caller-name`) can override specific fields. CLI flags take precedence over file values.

**Rationale:** Operators running repeated calls (benchmarks, demos) benefit from a reusable context file. One-off overrides are common ("same context, different goal").

### D6: Persona is auto-loaded from `identity.md` with project-then-home discovery

**Decision:** Before applying CLI flags, the `call` command checks for `identity.md` in this order:
1. `<cwd>/identity.md` — project-local persona (committed or gitignored)
2. `~/identity.md` — user-global persona

The first file found wins. Its contents populate `agent_name`, `agent_role`, `agent_tone`, and a new `agent_background` free-text field. CLI flags and `--context` YAML always override identity file values.

**File format:** YAML front matter for structured fields, optional markdown body for `agent_background`:

```markdown
---
name: Jordan
role: senior account manager at Acme Corp
tone: professional and empathetic
---

Jordan has 8 years of experience in enterprise software sales.
Always reference the customer's account tier when relevant.
```

If no front matter is present, the entire file is treated as `agent_background` only.

**Rationale:** Operators with a consistent persona (branded agent, specific role) shouldn't have to pass flags on every call. The two-level lookup mirrors the `.gitconfig` / `~/.gitconfig` convention developers already know. Project-local takes precedence so per-project personas override a global default without extra flags.

**Alternatives considered:**
- Only `~/identity.md` (no project-local): prevents per-project overrides without CLI flags.
- Named profiles (`--persona work`): more flexible but requires a registry; file convention is zero-config.
- Merge both files: ambiguous conflict resolution; first-wins is simple and predictable.

## Risks / Trade-offs

- **Prompt regression** → Existing calls that relied on the old hardcoded system prompt may behave differently. Mitigation: keep the old prompt text as the fallback when all optional fields are absent, so the default experience is identical.
- **Required field creep** → If too many fields become required, the confirmation step becomes friction. Mitigation: only `goal` is required by default; everything else is optional.
- **YAML context file exposure** → A context YAML might contain PII (caller name, account info). Mitigation: document this risk; do not log context file contents.
- **`identity.md` silently overrides intent** → An operator may forget a project-local `identity.md` exists and be confused by the active persona. Mitigation: the pre-call confirmation display always shows the source of each persona field (e.g., `agent_name: Jordan  [from identity.md]`).

## Migration Plan

1. Add `shuo/context.py` with `CallContext`, `load_identity_file()`, and `build_system_prompt()`
2. Update `shuo/language.py` to accept `CallContext` and call `build_system_prompt()`
3. Update `shuo/cli.py` `call` command: load identity file → apply `--context` YAML → apply CLI flags → confirm → dial
4. Update `shuo/call.py` `run_call()` to accept `CallContext` and pass it through
5. Deploy: no schema migrations, no infra changes. Rollback = revert commits.

## Open Questions

- Should `goal` be the only hard-required field, or should `agent_name` also be required? (Current proposal: `agent_name` defaults to "Alex", so it's effectively never missing.)
- Should `voice-agent serve` (inbound calls) accept a default `CallContext` too? Out of scope for now but architecturally possible.
