# Voice Agent — 5-Day Learning Guide

A structured onboarding path from high-level concepts to implementation details.
Each day builds on the previous one.

---

## Day 1 — System Overview: What It Does and How Data Flows

### What you're building on top of

This is a real-time AI voice agent platform. It enables an LLM-powered agent to make and receive phone calls via Twilio, with a target end-to-end latency of ~400ms from when the user stops speaking to when the agent starts playing audio.

### The audio pipeline (follow the bytes)

```
Caller (PSTN)
  → Twilio (converts PSTN to WebSocket, μ-law 8kHz audio)
  → FastAPI /ws  (shuo/web.py — WebSocket handler)
  → Deepgram Flux STT  (shuo/speech.py — produces transcripts)
  → Groq LLM  (shuo/language.py — streams tokens)
  → ElevenLabs TTS  (shuo/voice_elevenlabs.py — streams audio chunks)
  → AudioPlayer  (shuo/voice.py — assembles and sends back to Twilio)
  → Caller hears the response
```

The critical insight: **every stage is streaming**. Tokens flow from LLM to TTS as they arrive; audio chunks flow from TTS to the phone as they are synthesised. This is what makes ~400ms achievable.

### The three layers of the codebase

| Layer | Files | Role |
|-------|-------|------|
| **Core runtime** | `shuo/` | The actual call engine |
| **Supervisor UI** | `monitor/` | Dashboard for watching/taking over calls |
| **Simulator** | `simulator/` | YAML-driven IVR simulator for benchmarking |

### What happens when a call arrives

1. Twilio makes a GET/POST to `/twiml` — the server responds with TwiML XML that tells Twilio to open a WebSocket to `/ws`.
2. Twilio opens the WebSocket. `web.py` accepts it and calls `run_call()`.
3. `run_call()` wires together all components and drives an event loop until the call ends.
4. On disconnect, latency traces and telemetry summaries are saved.

### Environment and running it locally

```bash
cp .env.example .env        # fill in API keys
uv sync                     # install deps
./run.sh serve              # starts server + ngrok tunnel
./run.sh local-call         # two agents in process, no Twilio needed
```

### Day 1 exercise

Read `shuo/call.py` lines 1–23 (the module docstring) and then `shuo/web.py` lines 1–50. Trace the path of a single audio packet from `on_audio()` in `run_call()` through to `Transcriber.send()`. You don't need to understand everything — just identify the handoff points.

---

## Day 2 — The Pure Core: State Machine and Event Loop

### Why a state machine?

The call has exactly three states:

```
LISTENING   → waiting for the user to speak
RESPONDING  → agent is generating + playing audio
ENDING      → hangup in progress, drain audio
```

The state machine lives in `shuo/call.py` as a single pure function:

```python
def step(state: CallState, event: Event) -> Tuple[CallState, List[Action]]:
    ...
```

**Pure** means: no I/O, no side effects, no async. Given the same inputs you always get the same outputs. This makes it trivially testable — look at `tests/test_agent.py` to see how many edge cases are covered with no mocks at all.

### Events vs Actions

**Events** are things that happen:
- `CallStartedEvent` — phone connected
- `UserSpokeEvent(transcript)` — user finished a sentence
- `UserSpeakingEvent` — user just started speaking (barge-in trigger)
- `AgentDoneEvent` — agent finished playing audio
- `HangupEvent` — time to hang up

**Actions** are things to do in response:
- `StreamToSTTAction` — forward audio to Deepgram
- `StartTurnAction(transcript)` — kick off the LLM/TTS pipeline
- `CancelTurnAction` — interrupt the current agent turn (barge-in)

The event loop in `run_call()` is the shell around the pure core:

```python
while True:
    event = await queue.get()           # I/O
    state, actions = step(state, event) # PURE — this is where logic lives
    for action in actions:
        await dispatch(action)          # I/O
```

### How barge-in works

When `UserSpeakingEvent` arrives while in `RESPONDING` phase:
1. `step()` returns `(LISTENING, [CancelTurnAction()])`
2. `dispatch()` calls `agent.cancel_turn()`
3. `cancel_turn()` cancels the LLM task, cancels the TTS stream, stops audio playback
4. History is preserved — the partial LLM response is discarded but prior turns stay

The key invariant: barge-in is **suppressed in IVR mode** and **after hangup is decided**, because in those cases the remote party is automated or we want the goodbye to complete cleanly.

### Synthetic events: GreetEvent and HandbackEvent

These are not from external sources — they are injected into the queue by `run_call()` itself. This is elegant: even synthetic triggers go through `step()` so every state transition is logged and auditable.

- `GreetEvent` is injected after `CallStartedEvent` to trigger the opening agent turn.
- `HandbackEvent` is injected when a human supervisor finishes a takeover.

### TurnOutcome — the bridge between LLM and call control

`TurnOutcome` (in `call.py`) is what the LLM layer reports back:

```python
@dataclass(frozen=True)
class TurnOutcome:
    dtmf_digits:    Optional[str]  # press keypad digits
    hold_continue:  bool           # still on hold, do nothing
    emit_hold_start: bool          # hold music detected
    emit_hold_end:  bool           # person returned from hold
    hangup:         bool           # disconnect after this turn
    has_speech:     bool           # there's actual audio to play
```

Priority order in `agent.py`'s `_dispatch_outcome()`:
1. `hold_continue` → silent done, no audio
2. `dtmf_digits` → send digit tone, suppress speech
3. `has_speech` → flush TTS buffer and play
4. else → empty turn, silent done

### Day 2 exercises

1. Open `tests/test_agent.py`. Find the tests that cover barge-in. Note that they test `step()` directly — no I/O.
2. Manually trace what happens when: user speaks → `UserSpokeEvent` → `step()` → `StartTurnAction` → `dispatch()` → `agent.start_turn()`.
3. Find where `GreetEvent` is put into the queue in `run_call()` and understand the condition that controls whether it fires.

---

## Day 3 — The LLM/TTS/STT Pipeline: How a Turn is Executed

### LanguageModel (shuo/language.py)

`LanguageModel` wraps `pydantic-ai`'s `Agent` and adds:
- **Streaming** — tokens arrive via `on_token` callback as the LLM generates
- **Persistent history** — `self._history` survives across turns for the whole call
- **Tool calling** — five tools (`press_dtmf`, `signal_hold`, `signal_hold_continue`, `signal_hold_end`, `signal_hangup`)
- **Fallback text-tag protocol** — for models that don't support tool calling, tags like `[DTMF:2]` and `[HANGUP]` are parsed from text

The LLM is configured via the `LLM_MODEL` env var. Default: `groq:llama-3.3-70b-versatile`. The system detects whether the model supports tools via `_supports_tools()`.

**System prompt structure:**
```
base prompt (role + rules)
+ context suffix (goal, CallContext, or nothing)
+ language suffix (if non-English)
```

`resolve_outcome()` is called after the LLM finishes a turn. It reads the tool side-effects accumulated in `_TurnCtx`, then falls back to regex parsing of the raw text for leaked function-call syntax.

**Auto-hangup safety net:** if the LLM says "goodbye" without calling `signal_hangup()`, `resolve_outcome()` detects it via `_is_farewell()` and sets `hangup=True` anyway.

### Agent (shuo/agent.py)

`Agent` is the per-call coordinator. One instance lives for the whole call. A new `AudioPlayer` is created for each turn.

Turn lifecycle:
1. `start_turn(transcript)` — get TTS connection from pool, create `AudioPlayer`, optionally translate transcript, call `llm.start(message)`
2. As LLM tokens arrive → `_on_llm_token()` → suppressed tokens are blocked, real tokens go to `tts.send(token)`
3. When LLM finishes → `_on_llm_done()` → optionally translate full response, call `tts.flush()`
4. As TTS produces audio → `_on_tts_audio(base64)` → forwarded to `AudioPlayer`
5. When TTS is done → `_on_tts_done()` → DTMF queue is appended as tone audio
6. When all audio has played → `_on_playback_done()` → emits `AgentDoneEvent` (or `HangupEvent` if `_pending_hangup`)

**Latency milestones recorded in every turn:**
- `t0` — turn start
- `t_first_token` — LLM first token (tracks LLM latency)
- `t_first_audio` — TTS first audio chunk (tracks TTS latency)

### TranscriberPool and VoicePool (shuo/speech.py, shuo/voice.py)

Both use the same pattern: **pre-warm N connections at startup**, then lend them out per-turn.

Why? Cold-starting a Deepgram WebSocket takes ~900ms. Cold-starting an ElevenLabs TTS session takes ~200ms. Pre-warming hides these from the user's perceived latency.

```
at startup:  [conn1, conn2]  ← idle pool
on turn:     conn1 = pool.get()  ← lent out
turn done:   pool.release(conn1)  ← returned or replaced
```

`VoicePool` supports multiple TTS providers (ElevenLabs, Kokoro, Fish Audio, VibeVoice). The provider is selected at startup via the `TTS_PROVIDER` env var. Per-tenant overrides are also supported.

### Translation (shuo/translation.py)

When `CALLER_LANG` ≠ `CALLEE_LANG`:
- **Inbound:** user transcript is translated from `CALLER_LANG` to `CALLEE_LANG` before the LLM sees it
- **Outbound:** LLM response is translated from `CALLEE_LANG` to `CALLER_LANG` before TTS speaks it

With translation enabled, TTS is not fed tokens as they stream — instead the full LLM response is awaited, then translated, then sent to TTS in one batch. This trades per-token latency for translation quality.

Providers: `LLMTranslator` (Groq, default) or `DeepLTranslator` (requires `DEEPL_API_KEY`).

Control signals like `[CALL_STARTED]` and `[HANDBACK]` bypass translation entirely.

### Day 3 exercises

1. In `language.py`, read the two system prompts (`_PROMPT_WITH_TOOLS` and `_PROMPT_TEXT_TAGS`). Understand when each is used.
2. Trace a full turn from `agent.start_turn()` through to `AgentDoneEvent` being emitted. Write down each callback that fires.
3. Find where translation is inserted into the pipeline in `agent.py` (look in `start_turn` and `_on_llm_done`). Note how it's bypassed for DTMF and hold_continue turns.

---

## Day 4 — Infrastructure: HTTP Server, Phone Abstraction, and Multi-Tenancy

### web.py — The FastAPI server

Key routes:
- `GET/POST /twiml` — returns TwiML telling Twilio to open a WebSocket
- `WebSocket /ws` — receives the media stream, calls `run_call()`
- `POST /call/{phone}` — initiates an outbound call
- `GET /dashboard` — supervisor UI
- `GET /trace/latest` — last call latency trace as JSON

The WebSocket handler in `web.py` is where all the wiring happens: it creates the `VoicePool`, `TranscriberPool`, sets up the observer callback for the monitor, resolves the tenant, and calls `run_call()`.

**Signature validation:** Every Twilio webhook request is validated using `verify_twilio_signature()` as a FastAPI dependency. It reads the `X-Twilio-Signature` header and validates against `TWILIO_AUTH_TOKEN`. Skipped in dev when the token is not set.

### phone.py — The Phone abstraction

`TwilioPhone` and `LocalPhone` share a common interface:
```python
async def start(on_audio, on_call_started, on_call_ended): ...
async def send_audio(audio_bytes): ...
async def send_dtmf(digits): ...
async def hangup(): ...
async def stop(): ...
```

`LocalPhone` creates an in-process loopback — two `LocalPhone` instances share queues. This is what `voice-agent local-call` uses: no Twilio, no internet, two agents talking to each other in the same process.

`TwilioPhone` parses the Twilio media stream JSON protocol:
- Incoming: `{"event": "media", "media": {"payload": "<base64>"}}` → decoded to bytes → `on_audio` callback
- Incoming: `{"event": "start", ...}` → `on_call_started` callback
- Outgoing audio: base64-encoded μ-law chunks wrapped in Twilio's `media` JSON

### tenant.py — Multi-tenancy

`TenantConfig` holds per-tenant Twilio credentials, default goal, TTS provider override, voice ID override, and a list of allowed phone numbers.

`resolve_tenant()` determines which tenant owns an incoming call:
1. Match on `AccountSid` (distinct Twilio accounts)
2. Match on `To` phone number (shared Twilio account)
3. Single-tenant convenience fallback

To configure multi-tenancy: set `TENANTS_YAML` env var pointing to a YAML file listing tenants. Without it, a single `default` tenant is built from env vars.

### context.py — CallContext and dynamic system prompts

`CallContext` is a Pydantic model for rich outbound call configuration. When you POST to `/call/{phone}` with a JSON body, the fields become the agent's context.

`build_system_prompt(ctx, tools)` in `context.py` generates the system prompt from a `CallContext`. This is the extension point for per-call customisation — caller name, account details, specific instructions all come from here.

### monitor/ — Supervisor dashboard

The dashboard is a separate FastAPI router mounted at `/dashboard`. It:
- Maintains a registry of active calls
- Has a real-time event bus (SSE) for streaming call events to the UI
- Supports human takeover: pause the agent, let a human speak, then hand back
- Handback injects a `HandbackEvent` containing a summary of what the human said

### tracer.py and telemetry.py — Observability

`Tracer` records fine-grained per-turn latency spans (LLM, TTS, pool acquisition). Results are saved to `traces/` as JSON and exposed at `/trace/latest`.

`CallTelemetry` uses checkpoints (`CP` enum) to record wall-clock timestamps for the whole call lifecycle. The summary is logged at hangup.

### Day 4 exercises

1. In `web.py`, find where `run_call()` is invoked from the WebSocket handler. Identify all the arguments passed in and where they come from.
2. In `tenant.py`, trace how `resolve_tenant()` is used in `web.py`. What happens if no tenant matches?
3. Read `phone.py`'s `LocalPhone` class. Understand how two instances share queues to simulate a call without Twilio.

---

## Day 5 — Tests, Operations, and Extension Patterns

### Test architecture

The test suite has two layers:

**`tests/`** — pure unit tests, ~0.03s total, no I/O
- Test `step()` directly with fabricated events — no mocks needed because it's a pure function
- Test `LanguageModel.resolve_outcome()` with fabricated turn text and tool contexts
- Test tenant resolution, translation logic, CLI parsing

**`simulator/tests/`** — integration tests, ~1s total
- Test IVR flow parsing, YAML config loading, benchmark runner
- Use `LocalPhone` to run full call loops with a simulated IVR

Run all tests:
```bash
python -m pytest tests/ -v
python -m pytest simulator/tests/ -v
```

### Common development workflows

**Changing what the agent says:**
Edit the system prompt strings in `shuo/language.py`. `_PROMPT_WITH_TOOLS` is for Llama 3.3 (default), `_PROMPT_TEXT_TAGS` is for compound-beta models.

**Changing agent behaviour per call:**
Edit `shuo/context.py`. Add fields to `CallContext` and update `build_system_prompt()`.

**Adding a new TTS provider:**
1. Create `shuo/voice_yourprovider.py` implementing the same interface as `voice_elevenlabs.py` (async `send(text)`, `flush()`, `cancel()` + audio callback)
2. Add the provider to the factory in `shuo/voice.py`
3. Add `yourprovider` as a valid value for `TTS_PROVIDER`

**Running an outbound benchmark:**
```bash
voice-agent bench --dataset eval/scenarios/example_ivr.yaml
```
The simulator runs a call against a YAML-defined IVR flow and reports completion rate and latency.

**Inspecting latency:**
After a call, `GET /trace/latest` returns a JSON trace with all spans. The `tracer.py` save format is `traces/<stream_sid>.json`.

### Key invariants to preserve

1. **`step()` must remain pure** — never add I/O or async to it
2. **History survives cancel** — `cancel_turn()` cancels the LLM task but does not clear `self._history`; the next `start_turn` resumes from the correct history
3. **Barge-in is suppressed in IVR mode** — `ivr_mode()` callback controls this in `dispatch()`
4. **Hangup is two-step** — `HangupPendingEvent` blocks new turns; `HangupEvent` is emitted only after goodbye audio finishes
5. **Control signals bypass translation** — transcripts starting with `[CALL_STARTED]` or `[HANDBACK]` are never translated

### Known warnings (benign, do not fix)

- `websockets.legacy` deprecation — inside Deepgram's SDK, not our code
- FastAPI `on_event` deprecation — in `web.py` startup/shutdown; can migrate to `lifespan=` when convenient

### Extension ideas to explore

- **IVR mode:** Look at `simulator/` to understand how YAML flows define IVR trees. The `ivr_mode` callback in `run_call()` is what controls barge-in suppression.
- **Multi-language calls:** Set `CALLER_LANG`, `CALLEE_LANG`, `DEEPGRAM_LANGUAGE`. The translation pipeline handles the rest.
- **Custom CallContext:** POST to `/call/{phone}` with a rich JSON body — all fields appear in the system prompt via `build_system_prompt()`.
- **Pre-warmed pools:** To reduce the 900ms Deepgram cold-start to near-zero, a `TranscriberPool` with `pool_size > 1` is passed from `web.py` to `run_call()`.

### Day 5 exercise

1. Run the full test suite. Confirm 133 tests pass.
2. Run `./run.sh local-call` and observe the console output. Match each log line to the code that emits it.
3. Pick one test from `tests/test_agent.py` and understand every line — what event fires, what `step()` returns, what action is dispatched.
4. Read `monitor/server.py` to understand how the supervisor dashboard connects to the call loop via the observer callback in `run_call()`.

---

## Quick Reference

### File map

| File | One-line role |
|------|---------------|
| `shuo/call.py` | State machine + event/action types + `run_call()` event loop |
| `shuo/agent.py` | Per-turn LLM→TTS→playback coordinator |
| `shuo/language.py` | LLM streaming, tool calling, system prompts, `resolve_outcome()` |
| `shuo/speech.py` | Deepgram Flux STT, `TranscriberPool` |
| `shuo/voice.py` | `VoicePool`, `AudioPlayer`, DTMF tone generation |
| `shuo/voice_elevenlabs.py` | ElevenLabs TTS provider |
| `shuo/phone.py` | Phone abstraction: `TwilioPhone`, `LocalPhone`, `dial_out()` |
| `shuo/web.py` | FastAPI server, `/twiml`, `/ws`, `/call/{phone}` |
| `shuo/tenant.py` | Multi-tenant config: `TenantConfig`, `TenantStore`, `resolve_tenant()` |
| `shuo/context.py` | `CallContext` Pydantic model + `build_system_prompt()` |
| `shuo/translation.py` | Bidirectional translation: LLM and DeepL providers |
| `shuo/tracer.py` | Per-turn latency spans |
| `shuo/telemetry.py` | Call lifecycle checkpoints |
| `monitor/` | Supervisor dashboard |
| `simulator/` | YAML IVR simulator for benchmarking |

### State machine in one diagram

```
             ┌──────────────────────────────────────────┐
             │  UserSpeakingEvent (barge-in, no hold)   │
             ▼                                           │
        LISTENING ──UserSpokeEvent──► RESPONDING ────────┘
             ▲                           │
             └──────AgentDoneEvent───────┘
                                         │ HangupPendingEvent / HangupEvent
                                         ▼
                                       ENDING  (absorbs all events)
```

### Latency budget (approximate)

| Stage | Target |
|-------|--------|
| Deepgram STT (end-of-turn) | ~150ms |
| LLM first token (Groq) | ~100ms |
| TTS first chunk (ElevenLabs) | ~100ms |
| Audio buffering + Twilio | ~50ms |
| **Total** | **~400ms** |
