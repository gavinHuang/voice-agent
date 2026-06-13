# IVR Call Issues Report — VicRoads 131171

**Call trace:** `data/calls/default/MZ7cc135f6e43faba8bc2c46a263153b84.json`
**Goal:** Ask about eligibility conditions for rego fee discount
**Result:** Failed — 8 turns, manually hung up by user

---

## Issue 1 — Agent greeted the IVR

**What happened:** Turn 1: agent said "Hello, I'm Alex..." into the VicRoads IVR.

**Why:** No `--ivr` flag was used. `ivr_mode=False` causes `[CALL_STARTED]` to trigger a normal greeting turn instead of staying silent.

**Fix:** Added `--ivr` CLI flag. When set, `CALL_IVR_MODE=1` env var is set, which propagates `ivr_mode=True` through `twiml()` → `set_pending()` → `run_call()`. The agent's initial greeting is suppressed.

---

## Issue 2 — AMD (Answering Machine Detection) hanging up on IVR

**What happened:** Twilio's AMD classified VicRoads' IVR as a "machine" (`machine_start` / `machine_end_*`), triggering a `<Hangup/>` response in `web.py twiml()`.

**Fix 1:** `dial_out(ivr_mode=True)` skips the `machine_detection="Enable"` and `async_amd="true"` params entirely so AMD is never started.

**Fix 2:** Added `if not _ivr_mode` guard around the AMD hangup block in `twiml()` so even if an AMD result somehow arrives, it won't hang up an IVR call.

---

## Issue 3 — Each IVR sentence fragment triggered an agent turn

**What happened:** Turns 2–7: the agent responded to every IVR utterance with spoken text, because Deepgram fired `EndOfTurn` on each natural pause between IVR sentences.

**Why:** STT detects silence as end-of-turn. IVR systems pause between sentences. Each pause = new turn = agent tries to respond.

**Fix:** Added `ivr_context=True` parameter to `agent.start_turn()`. When set, the transcript is wrapped with `[IVR]` prefix and explicit tool-only instructions:
- Announcement / incomplete menu → `signal_hold_continue()` (silent passthrough)
- Complete menu option ("press X for Y") → `press_dtmf("X")`
- Auth request with unknown digits → `press_dtmf("0")` for operator

This routes through from `call.py dispatch()` reading `ivr_mode()`.

---

## Issue 4 — LLM lacked IVR-specific rules in system prompt

**What happened:** Even in IVR mode, the LLM had no instructions about how to handle IVR menus — it treated every transcript as a conversation.

**Fix:** Added comprehensive IVR navigation rules to `language.py` (`_PROMPT_WITH_TOOLS` and `_PROMPT_TEXT_TAGS`) and `context.py` (`build_system_prompt()` `ivr_rule`):
- Announcements → silent passthrough
- Partial menu → wait for complete option
- Complete menu + "press X" → DTMF only, no speech
- Auth/input without data → press 0 for operator

---

## Issue 5 — No fallback when IVR requests unknown authentication data

**What happened:** When the IVR asked for a driver's licence, the agent had no data and the STRICT SCOPE RULE prevented asking the caller for IDs. The call stalled with no way forward.

**Fix:** IVR rules now explicitly say: auth/input request without the info → `press_dtmf("0")` for operator.

---

## Files changed

| File | Change |
|------|--------|
| `shuo/cli.py` | Added `--ivr` flag; sets `CALL_IVR_MODE=1`; passes `ivr_mode=is_ivr` to `dial_out()`; 180s connect timeout for IVR |
| `shuo/web.py` | Reads `CALL_IVR_MODE` in `twiml()`; passes `ivr_mode` to `set_pending()`; skips AMD hangup when `_ivr_mode=True` |
| `shuo/agent.py` | Added `ivr_context: bool` to `start_turn()`; wraps transcript with `[IVR]` prefix and tool-only instructions |
| `shuo/call.py` | Passes `ivr_context=_ivr_ctx` through `dispatch()` → `agent.start_turn()` |
| `shuo/language.py` | Added IVR navigation rules to `_PROMPT_WITH_TOOLS` and `_PROMPT_TEXT_TAGS` |
| `shuo/context.py` | Updated `build_system_prompt()` `ivr_rule` with tool-specific and tag-specific IVR instructions |

---

## Usage

```bash
voice-agent call 131171 --goal "Asking about the eligibility condition to get a rego fee discount" --ivr --yes
```

## Verification status

All code fixes in place. Tests: 219/221 (3 pre-existing failures unrelated to this work).

Live call verification attempted but VicRoads 131171 returned Twilio `status: busy` — the number was at PSTN capacity and never answered. This is a line availability issue, not a code bug.
