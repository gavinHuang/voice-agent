## 1. CallContext Module

- [ ] 1.1 Create `shuo/context.py` with `CallContext` dataclass (all fields, types, defaults per spec)
- [ ] 1.2 Implement `CallContext.from_yaml(path)` class method for YAML deserialization
- [ ] 1.3 Implement `CallContext.to_yaml(path)` for YAML serialization
- [ ] 1.4 Implement `build_system_prompt(ctx: CallContext) -> str` — assembles system prompt from context fields only

## 2. Language Model Integration

- [ ] 2.1 Update `shuo/language.py` `LanguageModel` to accept a `CallContext` (or system prompt string) instead of inline prompt construction
- [ ] 2.2 Remove any hardcoded persona or goal text from `language.py` prompt assembly
- [ ] 2.3 Update `shuo/call.py` `run_call()` to accept `CallContext` and pass it to the language model

## 3. CLI Updates

- [ ] 3.1 Add `--context` flag to `voice-agent call` command for loading a YAML `CallContext` file
- [ ] 3.2 Add per-field CLI flags: `--agent-name`, `--agent-role`, `--agent-tone`, `--caller-name`, `--caller-context`, `--constraint` (repeatable), `--success-criteria`
- [ ] 3.3 Implement CLI flag override logic: YAML base values are overridden by explicit CLI flags
- [ ] 3.4 Add `--yes` / `-y` flag to bypass interactive confirmation

## 4. Pre-Call Confirmation Flow

- [ ] 4.1 Implement `confirm_context(ctx: CallContext, yes: bool) -> bool` in `shuo/context.py` — prints summary, prompts for missing required fields, asks "Proceed? [y/N]"
- [ ] 4.2 Format the context summary clearly (label + value per field, "(not set)" for absent optionals)
- [ ] 4.3 Prompt for `goal` if missing and `--yes` is not set; exit with error if `--yes` is set and goal is missing
- [ ] 4.4 Wire `confirm_context` into `voice-agent call` before the dial step

## 5. Tests

- [ ] 5.1 Unit test `CallContext` defaults and required-field validation
- [ ] 5.2 Unit test YAML round-trip serialization
- [ ] 5.3 Unit test `build_system_prompt` with full context and minimal context
- [ ] 5.4 Unit test `confirm_context` with `yes=True` (no prompt) and missing required field error path
- [ ] 5.5 Test that `run_call()` receives and uses `CallContext` (mock the language model)
