## Why

When an agent makes or receives a call, it often lacks structured context — it improvises caller identity, goal details, and situational facts that were never provided. This leads to hallucinated context, awkward conversations, and unreliable outcomes. Confirming required fields before dialing ensures the agent acts from ground truth, not guesswork.

## What Changes

- Introduce a `CallContext` schema: a typed, declarative set of fields (required + optional) that parameterize a call
- Add a default agent persona (`Alex`) with name, role, and tone pre-defined so callers hear a consistent identity
- Add a pre-call confirmation step in the CLI that validates all required fields are present, prompts for missing ones, and previews optional ones
- The `LanguageModel` system prompt is assembled from confirmed `CallContext` fields — no field is inferred or fabricated
- Optional fields are shown with defaults and can be accepted or overridden interactively

## Capabilities

### New Capabilities

- `call-context`: Typed field schema for call goals, agent identity, and situational context — drives system prompt assembly and pre-call validation
- `pre-call-confirmation`: Interactive CLI step that collects missing required fields, previews the call context, and requires explicit user confirmation before dialing

### Modified Capabilities

- `cli-call`: The `voice-agent call` command gains a `--context` flag and triggers the pre-call confirmation flow before initiating the call

## Impact

- `shuo/cli.py`: New `--context` flag and confirmation prompt added to `call` command
- `shuo/call.py` or `shuo/language.py`: System prompt assembly updated to consume `CallContext` fields
- New module `shuo/context.py`: defines `CallContext` dataclass, field schema, default persona, and prompt builder
- No breaking changes to WebSocket or Twilio protocol
- No new external dependencies required
