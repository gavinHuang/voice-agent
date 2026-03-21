# Phase 2: Bug Fixes - Research

**Researched:** 2026-03-21
**Domain:** Python asyncio concurrency, connection pool race conditions, call lifecycle management
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| BUG-01 | `_dtmf_pending` dict access is protected by an asyncio lock | Lock placement identified; two concurrent access sites mapped in server.py |
| BUG-02 | TTS pool eviction is atomic (TOCTOU race eliminated) | Exact TOCTOU window located in tts_pool.py; fix pattern identified |
| BUG-03 | Token observer callback runs in a non-blocking context (does not block LLM stream) | Blocking call site found in agent.py:333; asyncio fix pattern confirmed |
| BUG-04 | Calls with no activity for N seconds are automatically hung up (configurable timeout) | conversation.py loop structure analyzed; two valid injection approaches documented |
</phase_requirements>

---

## Summary

Phase 2 fixes four distinct correctness bugs in the voice agent before any feature work proceeds. Three are concurrency bugs (races between asyncio tasks sharing mutable state without locks), and one is a missing operational safety net (no call timeout). All four bugs exist in the current codebase and are independently fixable. Each fix is confined to a single file and requires no cross-module API changes.

The fixes are small in terms of lines changed but high in risk if skipped: the race conditions can corrupt call state silently, and the missing timeout causes resource leaks on dead calls that Twilio never cleans up. The fixes should be done before Phase 3 (CLI) and Phase 6 (agent framework migration) because those phases add more concurrent callers and new observer paths that would amplify each bug.

**Primary recommendation:** Fix bugs in dependency order — BUG-01 and BUG-02 first (shared global state), then BUG-03 (per-call observer), then BUG-04 (timeout scaffold). Each fix is independently testable with pytest-asyncio unit tests.

---

## Bug Analysis

### BUG-01: `_dtmf_pending` Race Condition

**File:** `shuo/shuo/server.py`
**Line of declaration:** 60 — `_dtmf_pending: dict = {}`

**What it is:** A module-level dictionary shared across all concurrent WebSocket handler coroutines. Two coroutines can interleave on the same `call_sid` if multiple rapid DTMF sequences or two simultaneous reconnects occur.

**Exact access sites:**

| Line | Operation | Context |
|------|-----------|---------|
| 628 | `_dtmf_pending[call_sid] = {...}` | `on_dtmf()` — write, called from conversation loop when agent emits DTMF |
| 638 | `_dtmf_pending.pop(call_sid, None)` | `get_saved_state()` — read+delete, called on new WebSocket connect |

**Race scenario:** Call A's `on_dtmf` writes `_dtmf_pending["CA123"]`. Simultaneously, Call A's reconnecting WebSocket handler is in `get_saved_state()` and calls `.pop("CA123")`. In CPython, `.pop()` on a dict is atomic at the C level, but the write at line 628 is not: it reads the dict, writes, and the `on_dtmf` function also reads `dashboard_registry` before writing — meaning a second DTMF event on the same call can clobber the first before the reconnect pops it.

The CONCERNS.md documents "Multiple rapid DTMF sequences" as an untested risk with "DTMF sequences sent out of order or lost" as the failure mode.

**Fix:** Add a module-level `asyncio.Lock` (`_dtmf_lock = asyncio.Lock()`). Wrap both the write in `on_dtmf` and the read+delete in `get_saved_state` with `async with _dtmf_lock:`. Both functions are already in async contexts (they are called from async code in the WebSocket handler), so the lock is compatible without restructuring.

**Confidence:** HIGH — access sites confirmed by code reading; fix pattern is standard asyncio.

---

### BUG-02: TTS Pool Eviction TOCTOU Race

**File:** `shuo/shuo/services/tts_pool.py`

**What it is:** A time-of-check/time-of-use (TOCTOU) race between the background `_fill_loop` task (which runs `_evict_stale`) and the caller task (which runs `get()`). Both access `self._ready` (a plain `list`) without a lock.

**Exact race window:**

```
Task A: get() -- line 89: while self._ready:
Task A: get() -- line 90: entry = self._ready.pop(0)   ← pop succeeds
                                                          ← asyncio yield point (await somewhere)
Task B: _fill_loop -- _evict_stale() -- line 173: for entry in self._ready:
Task B: _fill_loop -- calls entry.tts.cancel()          ← cancels the same entry Task A popped
Task A: get() -- line 94: entry.tts.bind(...)           ← binds a cancelled TTS connection
```

The `_evict_stale` method at lines 168-182 builds a `fresh` list and reassigns `self._ready = fresh`. Meanwhile, `get()` is also reading and popping from `self._ready`. In asyncio, these are not truly parallel (no threads), but `await` calls inside both paths create interleaving opportunities. Specifically, `await entry.tts.cancel()` inside `_evict_stale` yields control, and `get()` can execute during that yield.

A second scenario: `get()` pops an entry (line 90), checks age < ttl (line 93), and the entry passes — but `_evict_stale` had already determined that same entry was stale and called `cancel()` on it before `get()` ran. The entry's TTS connection is now dead when `bind()` is called.

**Fix:** Add `asyncio.Lock self._lock` to `TTSPool.__init__`. Wrap `self._ready` access in both `get()` and `_evict_stale()` with `async with self._lock:`. The lock must be held only for the list mutation, not during `await tts.cancel()` (to avoid holding a lock during I/O). Pattern:

```python
async with self._lock:
    entries_to_cancel = [e for e in self._ready if age_expired(e)]
    self._ready = [e for e in self._ready if not age_expired(e)]
# cancel outside lock
for entry in entries_to_cancel:
    await entry.tts.cancel()
```

**Confidence:** HIGH — race window confirmed by tracing `await` points in both code paths.

---

### BUG-03: Token Observer Callback Blocks LLM Stream

**File:** `shuo/shuo/agent.py`
**Line:** 332-333

**What it is:** The token observer is typed as `Callable[[str], None]` (synchronous) and is called directly inside the `async def _on_llm_token` coroutine:

```python
if self._on_token_observed:
    self._on_token_observed(clean_text)   # line 333 — sync call, no await
```

`_on_llm_token` is itself awaited by `LLMService._generate` on every token (line 131: `await self._on_token(token)`). So the entire LLM streaming loop stalls until `_on_token_observed` returns.

**Current observer implementation** (server.py:584-591): The observer calls `dashboard_bus.publish_global(tagged)` which does `q.put_nowait(event)` — this is O(n) over connected dashboard clients and does no I/O, so it is fast today. The bug is latent: any future observer that does I/O (WebSocket send, database write, metric flush) will stall the LLM stream for that I/O's duration on every token.

**Why this is asyncio blocking, not thread blocking:** There is only one thread. A slow sync callback in `_on_llm_token` occupies the event loop without yielding, delaying all other coroutines — including TTS audio delivery — for the duration of the callback.

**Fix:** Two valid approaches:

1. **`asyncio.get_event_loop().call_soon(callback, arg)`** — schedules the sync callback to run after the current coroutine yields. Zero latency impact on the LLM stream. Correct when the observer is guaranteed synchronous and fast.

2. **Change type to `Callable[[str], Awaitable[None]]` + `await`** — makes the observer protocol async-first. Correct when callers may need to do I/O. Requires updating the lambda in conversation.py to be `async def`.

The requirement says "observer runs in a non-blocking context." The safest fix that satisfies the requirement without changing the observer type signature is wrapping the call in `asyncio.get_event_loop().call_soon(self._on_token_observed, clean_text)` — fire-and-forget scheduling. This ensures the observer never blocks the token loop regardless of what the observer does.

If the observer type is changed to async, the lambda in conversation.py (line 148-151) must be updated to an async lambda or a named async function.

**Confidence:** HIGH — call site confirmed; asyncio scheduling behavior is well-documented.

---

### BUG-04: No Call Timeout

**File:** `shuo/shuo/conversation.py`
**Location:** `run_conversation()` main while loop (lines 109-247)

**What it is:** The conversation loop only exits when `StreamStopEvent` or `HangupRequestEvent` arrives. If Twilio never sends `StreamStopEvent` (network drop, WebSocket half-close, carrier-side hangup without notification), the coroutine runs forever. The ANALYSIS.md explicitly flags: "No call timeout — Hung calls leak forever if Twilio never sends StreamStopEvent."

**Where to inject the timeout:** The event queue `get` at line 111:

```python
event = await event_queue.get()
```

This is the single blocking point in the loop. Wrapping it in `asyncio.wait_for()` with a per-call configurable deadline is the minimal, safe fix.

**Two valid approaches:**

1. **`asyncio.wait_for` on `event_queue.get()`:** Replace line 111 with:
   ```python
   try:
       event = await asyncio.wait_for(event_queue.get(), timeout=inactivity_seconds)
   except asyncio.TimeoutError:
       # No event for N seconds → treat as hangup
       await isp.hangup()
       break
   ```
   Simple, no new tasks, single responsibility. Resets on every event (including MediaEvents), which may be too sensitive — audio packets arrive continuously, so the timeout would rarely fire during a real call.

2. **Separate watchdog task:** A background task tracks `last_activity_time` and fires a synthetic `HangupRequestEvent` if `now - last_activity_time > timeout`. This allows defining "activity" more precisely (e.g., only FluxEndOfTurnEvent, not MediaEvents) — appropriate for detecting truly dead calls where audio is flowing but no speech is detected.

**Recommendation:** Use approach 2 (watchdog task). A call receiving only silence audio forever should still be caught, while a call actively processing audio turns should not time out. The watchdog updates `last_activity_time` on meaningful events (StreamStart, FluxEndOfTurn, AgentTurnDone) and puts a `StreamStopEvent` (or new `InactivityTimeoutEvent`) on the queue when the deadline expires.

**Configuration:** Read from env var `CALL_INACTIVITY_TIMEOUT` with a default of 300 seconds (5 minutes). The requirement says "configurable number of seconds."

**Confidence:** HIGH — loop structure confirmed by reading; asyncio.wait_for and watchdog patterns are well-established.

---

## Standard Stack

### Core (all already in project)
| Library | Version | Purpose | Notes |
|---------|---------|---------|-------|
| asyncio | stdlib | Locks, tasks, wait_for | No new dependency |
| pytest | existing | Test runner | See tests/ directory |
| pytest-asyncio | existing (inferred) | Async test support | Used in test_isp.py |

**No new dependencies required.** All four fixes use Python stdlib asyncio primitives.

### Patterns Used

| Pattern | Used For | asyncio API |
|---------|----------|-------------|
| `asyncio.Lock` | BUG-01, BUG-02 | `asyncio.Lock()`, `async with lock:` |
| `call_soon` / task scheduling | BUG-03 | `asyncio.get_event_loop().call_soon()` or fire-and-forget task |
| `asyncio.wait_for` | BUG-04 (approach 1) | `asyncio.wait_for(coro, timeout=N)` |
| `asyncio.create_task` + cancel | BUG-04 (approach 2, watchdog) | `asyncio.create_task()`, `task.cancel()` |

---

## Architecture Patterns

### Pattern 1: asyncio.Lock for Shared Mutable State (BUG-01, BUG-02)

The project already uses asyncio throughout. The standard fix for shared mutable state between concurrent coroutines is `asyncio.Lock`. The lock must be:
- Created once at module level (BUG-01) or instance level (BUG-02)
- Held only during the critical section (list read/write), NOT during I/O awaits
- Compatible with the existing code style — no restructuring needed

```python
# BUG-01 pattern (module-level in server.py)
_dtmf_lock: asyncio.Lock = asyncio.Lock()

async def on_dtmf(digits: str) -> None:
    async with _dtmf_lock:
        _dtmf_pending[call_sid] = {...}

def get_saved_state(call_sid: str):  # called from async context
    async with _dtmf_lock:           # must be awaited at call site
        saved_dtmf = _dtmf_pending.pop(call_sid, None)
    ...
```

Note: `get_saved_state` is currently a sync function called from an async context. It must become `async def` to use `async with`. The call site at conversation.py:120 already uses `await`-compatible patterns.

### Pattern 2: Non-blocking Observer (BUG-03)

Fire-and-forget via `call_soon` keeps the observer outside the hot path:

```python
# In agent.py _on_llm_token
if self._on_token_observed:
    asyncio.get_event_loop().call_soon(self._on_token_observed, clean_text)
```

If the observer type is upgraded to async (future-proofing), use:
```python
if self._on_token_observed:
    asyncio.create_task(self._on_token_observed(clean_text))
```

### Pattern 3: Watchdog Task (BUG-04)

```python
async def _inactivity_watchdog(
    event_queue: asyncio.Queue,
    timeout_seconds: float,
    activity_events: set,
) -> None:
    last_seen = asyncio.get_event_loop().time()
    while True:
        await asyncio.sleep(1.0)
        if asyncio.get_event_loop().time() - last_seen > timeout_seconds:
            await event_queue.put(StreamStopEvent())
            return
```

The watchdog is created after `StreamStartEvent` and cancelled in the `finally` block alongside flux/agent cleanup.

### Anti-Patterns to Avoid

- **Holding a lock during `await`:** Lock the list mutation only; release before any I/O. Holding `_dtmf_lock` during a WebSocket call would serialize all calls.
- **Using threading.Lock in asyncio code:** Always use `asyncio.Lock`, never `threading.Lock` in coroutines.
- **Module-level Lock initialized outside the event loop:** `asyncio.Lock()` must be created after the event loop starts (i.e., not at import time with `asyncio.Lock()` at module top in Python < 3.10). In Python 3.10+ this is safe. Verify Python version or create the lock lazily.
- **`asyncio.wait_for` on `event_queue.get()` resetting on media packets:** If timeout resets on every `MediaEvent`, a silent-but-connected call (streaming silence) will never time out. Use a watchdog with selective activity events.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Mutual exclusion | Custom flag/sentinel dict | `asyncio.Lock` | Handles cancellation, re-entrancy, stack unwinding correctly |
| Timeout cancellation | Manual `time.time()` polling | `asyncio.wait_for` or `asyncio.Task.cancel()` | Integrates with asyncio cancellation chain |
| Non-blocking dispatch | Thread pool / executor | `call_soon` or `create_task` | No threads needed; event loop handles scheduling |

**Key insight:** asyncio's cooperative scheduler means that "concurrent" in this codebase means "interleaved at await points." All three concurrency fixes need only standard asyncio primitives — no threads, no queues, no external libraries.

---

## Common Pitfalls

### Pitfall 1: `asyncio.Lock` Created at Import Time (Python < 3.10)

**What goes wrong:** `_dtmf_lock = asyncio.Lock()` at module top creates the lock bound to whatever event loop existed at import time (or raises `DeprecationWarning`). In Python 3.10+, the default loop is set at startup and this is safe.
**How to avoid:** Check the Python version in requirements.txt or pyproject.toml. If < 3.10, initialize the lock inside the `startup_warmup` function or lazily on first use.
**Warning signs:** `DeprecationWarning: There is no current event loop` at startup.

### Pitfall 2: Making `get_saved_state` Async Breaks the Call Site

**What goes wrong:** `get_saved_state` is currently a sync callback passed to `run_conversation`. If it becomes `async def`, the call site at conversation.py:120 (`saved = get_saved_state(...)`) must become `saved = await get_saved_state(...)`. Missing the `await` silently returns a coroutine object instead of the dict.
**How to avoid:** Update both the function definition and the call site atomically. Add a type annotation `get_saved_state: Optional[Callable[[str], Awaitable[Optional[dict]]]]` to `run_conversation`'s signature.
**Warning signs:** `saved` is a coroutine object; `saved["history"]` raises `TypeError`.

### Pitfall 3: TTS Pool Lock Held During `await entry.tts.cancel()`

**What goes wrong:** If the lock is held while awaiting `tts.cancel()`, every `get()` call blocks for the full cancel duration. On a pool of size 2 with 10 concurrent calls, this serializes TTS access.
**How to avoid:** Collect entries to evict while holding the lock, then release the lock, then cancel them. The "collect then act" pattern is standard for this.
**Warning signs:** TTS latency spikes visible in tracer output; `tts_pool` span times increase under load.

### Pitfall 4: Watchdog Task Not Cancelled on Normal Call Termination

**What goes wrong:** If the watchdog task is not cancelled in the `finally` block of `run_conversation`, it continues running after the call ends, eventually trying to put to a queue that's garbage collected.
**How to avoid:** Store the watchdog task reference; cancel and await it in the `finally` block alongside `agent.cleanup()` and `flux.stop()`.
**Warning signs:** `RuntimeWarning: coroutine was never awaited` or spurious `asyncio.QueueFull` errors after call ends.

### Pitfall 5: Inactivity Timeout Firing During Hold Music

**What goes wrong:** The watchdog counts down during hold music periods, where the agent is intentionally silent for potentially minutes. The timeout expires and hangs up a valid in-progress call.
**How to avoid:** Update `last_activity_time` on `HoldStartEvent` (treat receiving hold music as activity). Optionally use a separate, longer timeout for hold periods.
**Warning signs:** Calls placed on hold are being hung up after the configured timeout.

---

## Code Examples

### asyncio.Lock for Module-Level Dict

```python
# server.py — module level
_dtmf_pending: dict = {}
_dtmf_lock: asyncio.Lock  # initialized in startup_warmup or at first use

@app.on_event("startup")
async def startup_warmup() -> None:
    global _dtmf_lock
    _dtmf_lock = asyncio.Lock()
    # ... rest of startup

# on_dtmf — write path
async def on_dtmf(digits: str) -> None:
    async with _dtmf_lock:
        _dtmf_pending[call_sid] = {"history": ..., "goal": ..., "phone": ..., "ivr_mode": True}

# get_saved_state — read+delete path (must become async)
async def get_saved_state(call_sid: str) -> Optional[dict]:
    async with _dtmf_lock:
        saved_dtmf = _dtmf_pending.pop(call_sid, None)
    # rest of function unchanged
```

### TTSPool Lock (Collect-Then-Act)

```python
# tts_pool.py
def __init__(self, pool_size: int = 1, ttl: float = 8.0):
    ...
    self._lock = asyncio.Lock()

async def _evict_stale(self) -> None:
    now = time.monotonic()
    stale: List[_Entry] = []
    async with self._lock:
        fresh: List[_Entry] = []
        for entry in self._ready:
            if now - entry.created_at < self._ttl:
                fresh.append(entry)
            else:
                stale.append(entry)
        self._ready = fresh
    # Cancel outside lock — I/O during lock hold is an anti-pattern
    for entry in stale:
        await entry.tts.cancel()

async def get(self, on_audio, on_done):
    while True:
        async with self._lock:
            if self._ready:
                entry = self._ready.pop(0)
            else:
                entry = None
        if entry is None:
            break
        age = time.monotonic() - entry.created_at
        if age < self._ttl:
            entry.tts.bind(on_audio, on_done)
            self._trigger_fill()
            return entry.tts
        else:
            await entry.tts.cancel()  # outside lock
    # No warm connections — fresh connect
    tts = create_tts(on_audio=on_audio, on_done=on_done)
    await tts.start()
    self._trigger_fill()
    return tts
```

### Non-Blocking Token Observer

```python
# agent.py _on_llm_token (BUG-03)
if clean_text:
    self._tts_had_text = True
    await self._tts.send(clean_text)
    if self._on_token_observed:
        # Schedule on event loop — does not block current coroutine
        asyncio.get_event_loop().call_soon(self._on_token_observed, clean_text)
```

### Watchdog Task (BUG-04)

```python
# conversation.py
INACTIVITY_TIMEOUT = float(os.getenv("CALL_INACTIVITY_TIMEOUT", "300"))

async def _inactivity_watchdog(
    event_queue: asyncio.Queue,
    timeout: float,
) -> None:
    """Hang up if no meaningful call activity for `timeout` seconds."""
    last_active = asyncio.get_event_loop().time()
    try:
        while True:
            await asyncio.sleep(5.0)  # check every 5 seconds
            if asyncio.get_event_loop().time() - last_active > timeout:
                logger.warning(f"Inactivity timeout ({timeout}s) — hanging up")
                await event_queue.put(HangupRequestEvent())
                return
    except asyncio.CancelledError:
        pass

# Inside run_conversation, after StreamStartEvent:
watchdog = asyncio.create_task(
    _inactivity_watchdog(event_queue, INACTIVITY_TIMEOUT)
)

# In finally block:
if watchdog and not watchdog.done():
    watchdog.cancel()
    try:
        await watchdog
    except asyncio.CancelledError:
        pass
```

Note: The watchdog's `last_active` timestamp update needs to be driven by events — either pass a shared `asyncio.Event` or a mutable container, or restructure as a class. A simple approach is a one-element list `[time()]` passed by reference.

---

## Validation Architecture

> `nyquist_validation` is enabled in `.planning/config.json`.

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio |
| Config file | None detected — add `pytest.ini` or inline markers |
| Quick run command | `cd shuo && python -m pytest tests/ -x -q` |
| Full suite command | `cd shuo && python -m pytest tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| BUG-01 | Concurrent writes to `_dtmf_pending` don't corrupt state | Unit (asyncio) | `python -m pytest tests/test_bug_fixes.py::test_dtmf_lock_concurrent -x` | No — Wave 0 |
| BUG-01 | Sequential write then read returns correct value | Unit | `python -m pytest tests/test_bug_fixes.py::test_dtmf_pending_sequential -x` | No — Wave 0 |
| BUG-02 | `TTSPool.get()` never returns an already-cancelled entry | Unit (asyncio) | `python -m pytest tests/test_bug_fixes.py::test_tts_pool_eviction_atomic -x` | No — Wave 0 |
| BUG-02 | Concurrent `get()` and `_evict_stale()` do not double-cancel | Unit (asyncio) | `python -m pytest tests/test_bug_fixes.py::test_tts_pool_concurrent_evict -x` | No — Wave 0 |
| BUG-03 | Token observer does not delay `_on_llm_token` return | Unit (asyncio, timing) | `python -m pytest tests/test_bug_fixes.py::test_token_observer_nonblocking -x` | No — Wave 0 |
| BUG-04 | Call is hung up after inactivity timeout | Unit (asyncio, mock queue) | `python -m pytest tests/test_bug_fixes.py::test_inactivity_watchdog_fires -x` | No — Wave 0 |
| BUG-04 | Watchdog is cancelled cleanly on normal call end | Unit | `python -m pytest tests/test_bug_fixes.py::test_watchdog_cancelled_on_stop -x` | No — Wave 0 |
| BUG-04 | Timeout is configurable via env var | Unit | `python -m pytest tests/test_bug_fixes.py::test_inactivity_timeout_env_var -x` | No — Wave 0 |

### Sampling Rate

- **Per task commit:** `cd shuo && python -m pytest tests/ -x -q`
- **Per wave merge:** `cd shuo && python -m pytest tests/ -v`
- **Phase gate:** Full suite green (including the 26 pre-existing tests) before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `shuo/tests/test_bug_fixes.py` — covers BUG-01 through BUG-04 (8 test cases)
- [ ] `shuo/pytest.ini` or `pyproject.toml [tool.pytest.ini_options]` — set `asyncio_mode = auto` for pytest-asyncio

*(Pre-existing tests in `test_update.py`, `test_isp.py`, `test_ivr_barge_in.py` must continue to pass — they are the regression guard.)*

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Plain dict shared across coroutines | `asyncio.Lock` + dict | This phase | Eliminates DTMF corruption |
| Unguarded list in connection pool | Lock + collect-then-cancel | This phase | Eliminates use-after-cancel |
| Sync observer in async hot path | `call_soon` scheduling | This phase | LLM stream no longer observer-bound |
| Unbounded call lifetime | Watchdog task with env-config timeout | This phase | Dead calls self-clean |

---

## Open Questions

1. **Python version in production**
   - What we know: `asyncio.Lock()` at module top is safe in Python 3.10+; prior versions require lazy init
   - What's unclear: The repo has no `pyproject.toml` or `.python-version` file specifying the version
   - Recommendation: Check `requirements.txt` for a Python version pin; if absent, initialize `_dtmf_lock` inside `startup_warmup` to be safe across all versions

2. **`get_saved_state` signature change ripple**
   - What we know: Making it `async def` changes the `run_conversation` callable type; the existing `LocalISP`/mock tests use a sync version
   - What's unclear: Whether any test fixture or mock passes a sync `get_saved_state` that would break
   - Recommendation: Audit `test_isp.py` and `test_ivr_barge_in.py` for usage; update mocks if needed

3. **Inactivity definition for BUG-04**
   - What we know: Audio `MediaEvent`s arrive continuously even on silent calls; meaningful activity is FluxEndOfTurn and StreamStart
   - What's unclear: Should `HoldStartEvent` reset the timer? (On-hold calls are intentionally quiet)
   - Recommendation: Reset timer on StreamStart, FluxEndOfTurnEvent, AgentTurnDoneEvent, HoldStartEvent, HoldEndEvent. Do NOT reset on MediaEvent.

4. **BUG-03 observer type: `call_soon` vs async upgrade**
   - What we know: The current observer (`dashboard_bus.publish_global`) is synchronous and fast; `call_soon` is sufficient today
   - What's unclear: Phase 5 (Security) may add WebSocket send to the observer path, which is async I/O
   - Recommendation: Use `call_soon` for BUG-03 fix now; note that Phase 5/6 may require upgrading the observer to async. Document in code.

---

## Sources

### Primary (HIGH confidence)

- Direct code reading: `shuo/shuo/server.py` lines 55-60, 584-638 — `_dtmf_pending` declaration and all access sites
- Direct code reading: `shuo/shuo/services/tts_pool.py` full file — `get()` and `_evict_stale()` interaction
- Direct code reading: `shuo/shuo/agent.py` lines 132, 303-333 — `on_token_observed` type and call site
- Direct code reading: `shuo/shuo/conversation.py` full file — loop structure, no timeout present
- Direct code reading: `shuo/shuo/services/llm.py` lines 123-131 — `await self._on_token(token)` confirms blocking
- Direct code reading: `dashboard/bus.py` lines 71-77 — `publish_global` is synchronous

### Secondary (MEDIUM confidence)

- `shuo/ANALYSIS.md` — independently identified all four bugs with file/line references
- `.planning/codebase/CONCERNS.md` — corroborates DTMF and pool race conditions

### Tertiary (LOW confidence)

- Python asyncio documentation (training knowledge, not freshly fetched) — `asyncio.Lock`, `call_soon`, `wait_for` semantics

---

## Metadata

**Confidence breakdown:**
- Bug locations: HIGH — confirmed by direct code reading of all four files
- Fix patterns: HIGH — standard asyncio primitives, no external library uncertainty
- Test strategy: HIGH — pytest-asyncio is already in use; unit tests are feasible for all four bugs
- Watchdog design: MEDIUM — activity event selection (which events reset the timer) involves a judgment call

**Research date:** 2026-03-21
**Valid until:** 2026-06-21 (stable asyncio stdlib — no expiry concern)
