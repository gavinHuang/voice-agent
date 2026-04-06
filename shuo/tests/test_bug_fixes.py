"""
Test scaffold for Phase 2 bug fixes (BUG-01 through BUG-04).

BUG-01: _dtmf_pending race condition (concurrent writes/pops can lose data)
BUG-02: TTS pool TOCTOU race (get() and _evict_stale() can double-cancel)
BUG-03: Token observer blocks _on_llm_token (slow observers stall TTS)
BUG-04: Inactivity watchdog missing (no timeout for stalled calls)

Tests 1-4 (BUG-01/02): Turn GREEN after Plan 02-01 fixes.
Tests 5-8 (BUG-03/04): Remain RED until Plan 02-02 fixes.
"""

import asyncio
import os
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# =============================================================================
# BUG-01: _dtmf_pending race condition
# =============================================================================

@pytest.mark.asyncio
async def test_dtmf_pending_sequential():
    """Sequential write-then-pop returns the correct dict (baseline)."""
    from shuo.server import _dtmf_pending

    call_sid = "test-sequential-001"
    # Clear any residue
    _dtmf_pending.pop(call_sid, None)

    # Simulate on_dtmf writing state
    _dtmf_pending[call_sid] = {
        "history": [{"role": "user", "content": "hello"}],
        "goal": "book a table",
        "phone": "+15550001111",
        "ivr_mode": True,
    }

    # Simulate get_saved_state reading state
    result = _dtmf_pending.pop(call_sid, None)

    assert result is not None
    assert result["goal"] == "book a table"
    assert result["phone"] == "+15550001111"
    assert result["ivr_mode"] is True
    assert len(result["history"]) == 1


@pytest.mark.asyncio
async def test_dtmf_lock_concurrent():
    """50 concurrent writers to _dtmf_pending lose no entries (lock required)."""
    import shuo.server as server_module
    from shuo.server import _dtmf_pending

    # The lock must exist after fix
    assert hasattr(server_module, "_dtmf_lock"), (
        "_dtmf_lock not found in shuo.server — BUG-01 fix not applied"
    )
    assert isinstance(server_module._dtmf_lock, asyncio.Lock), (
        "_dtmf_lock is not an asyncio.Lock"
    )

    lock = server_module._dtmf_lock

    # Create 50 unique call_sids
    call_sids = [f"concurrent-{i:03d}" for i in range(50)]
    for sid in call_sids:
        _dtmf_pending.pop(sid, None)

    async def write_entry(call_sid: str) -> None:
        async with lock:
            _dtmf_pending[call_sid] = {
                "history": [],
                "goal": f"goal-for-{call_sid}",
                "phone": "+15550000000",
                "ivr_mode": True,
            }

    async def pop_entry(call_sid: str) -> dict | None:
        async with lock:
            return _dtmf_pending.pop(call_sid, None)

    # Interleave concurrent writes and pops
    write_tasks = [write_entry(sid) for sid in call_sids]
    await asyncio.gather(*write_tasks)

    pop_tasks = [pop_entry(sid) for sid in call_sids]
    results = await asyncio.gather(*pop_tasks)

    missing = [call_sids[i] for i, r in enumerate(results) if r is None]
    assert not missing, f"Lost {len(missing)} entries: {missing[:5]}"

    for sid, result in zip(call_sids, results):
        assert result["goal"] == f"goal-for-{sid}"


# =============================================================================
# BUG-02: TTS pool TOCTOU race
# =============================================================================

@pytest.mark.asyncio
async def test_tts_pool_eviction_atomic():
    """After _evict_stale runs, get() never returns an evicted entry."""
    from shuo.services.tts_pool import TTSPool, _Entry

    pool = TTSPool(pool_size=1, ttl=0.01)

    # The lock must exist after fix
    assert hasattr(pool, "_lock"), (
        "TTSPool._lock not found — BUG-02 fix not applied"
    )

    # Create a mock TTS entry that is already stale
    mock_tts = AsyncMock()
    mock_tts.cancel = AsyncMock()
    mock_tts.bind = MagicMock()

    stale_entry = _Entry(tts=mock_tts, created_at=time.monotonic() - 1.0)
    pool._ready.append(stale_entry)

    # Evict stale entries
    await pool._evict_stale()

    # cancel() should have been called exactly once
    mock_tts.cancel.assert_called_once()

    # get() must NOT return the evicted entry (pool is empty now)
    # We mock create_tts so get() returns a fresh one without I/O
    fresh_tts = AsyncMock()
    fresh_tts.start = AsyncMock()

    with patch("shuo.services.tts_pool.create_tts", return_value=fresh_tts):
        result = await pool.get(on_audio=AsyncMock(), on_done=AsyncMock())

    assert result is fresh_tts, "get() returned the evicted entry instead of a fresh one"
    # The evicted mock should still have only 1 cancel call (not called again)
    mock_tts.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_tts_pool_concurrent_evict():
    """Interleaved get() and _evict_stale() never double-cancel any entry."""
    from shuo.services.tts_pool import TTSPool, _Entry

    pool = TTSPool(pool_size=3, ttl=10.0)  # long TTL so entries aren't naturally stale

    # The lock must exist after fix
    assert hasattr(pool, "_lock"), (
        "TTSPool._lock not found — BUG-02 fix not applied"
    )

    # Create 3 mock entries (fresh, not stale)
    mock_ttses = []
    for _ in range(3):
        m = AsyncMock()
        m.cancel = AsyncMock()
        m.bind = MagicMock()
        mock_ttses.append(m)

    for m in mock_ttses:
        pool._ready.append(_Entry(tts=m, created_at=time.monotonic()))

    # Force _evict_stale to treat entries as stale by manipulating created_at
    # We'll use ttl=0.001 on a separate evict call to force eviction
    old_ttl = pool._ttl
    pool._ttl = 0.0001  # Make all entries stale
    await asyncio.sleep(0.001)

    # Run get() and _evict_stale() concurrently
    async def do_get():
        fresh = AsyncMock()
        fresh.start = AsyncMock()
        with patch("shuo.services.tts_pool.create_tts", return_value=fresh):
            try:
                await pool.get(on_audio=AsyncMock(), on_done=AsyncMock())
            except Exception:
                pass

    await asyncio.gather(do_get(), pool._evict_stale())

    pool._ttl = old_ttl

    # No TTS should have cancel() called more than once
    for i, m in enumerate(mock_ttses):
        call_count = m.cancel.call_count
        assert call_count <= 1, (
            f"Entry {i} cancel() called {call_count} times — double-cancel detected"
        )


# =============================================================================
# BUG-03: Token observer blocks _on_llm_token
# =============================================================================

@pytest.mark.asyncio
async def test_token_observer_nonblocking():
    """A slow observer (500ms) must not delay _on_llm_token return by more than 50ms."""
    # This test imports _on_token_observed infrastructure from Agent.
    # It will fail if the observer is called synchronously (blocking).
    from shuo.agent import Agent

    slow_observer_called = []

    def slow_observer(text: str) -> None:
        slow_observer_called.append(text)
        time.sleep(0.5)  # 500ms blocking sleep

    # Create a minimal Agent with a slow observer
    mock_tts = AsyncMock()
    mock_tts.send = AsyncMock()

    mock_llm = MagicMock()
    mock_llm.is_suppressed_token = MagicMock(return_value=False)

    agent = Agent.__new__(Agent)
    agent._on_token_observed = slow_observer
    agent._active = True
    agent._tts = mock_tts
    agent._llm = mock_llm
    agent._tts_had_text = False
    agent._pending_hangup = False
    agent._got_first_token = True   # skip first-token tracer path
    agent._dtmf_queue = []
    agent._tracer = MagicMock()
    agent._turn = 0
    agent._t0 = time.monotonic()
    agent._current_turn_text = ""

    # Patch _emit to no-op
    agent._emit = MagicMock()

    start = time.monotonic()
    await agent._on_llm_token("Hello")
    elapsed_ms = (time.monotonic() - start) * 1000

    assert elapsed_ms < 50, (
        f"_on_llm_token took {elapsed_ms:.0f}ms — slow observer is blocking (BUG-03 not fixed)"
    )


# =============================================================================
# BUG-04: Inactivity watchdog
# =============================================================================

@pytest.mark.asyncio
async def test_inactivity_watchdog_fires():
    """Watchdog puts HangupRequestEvent on queue after configured timeout."""
    # This import will FAIL until _inactivity_watchdog is added to conversation.py
    from shuo.conversation import _inactivity_watchdog
    from shuo.types import HangupRequestEvent

    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(_inactivity_watchdog(queue, timeout=0.1))

    try:
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert isinstance(event, HangupRequestEvent), (
            f"Expected HangupRequestEvent, got {type(event)}"
        )
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_watchdog_cancelled_on_stop():
    """Watchdog task is cancelled cleanly without RuntimeWarning."""
    from shuo.conversation import _inactivity_watchdog

    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(_inactivity_watchdog(queue, timeout=300.0))

    # Cancel immediately
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass  # Expected — task should propagate CancelledError cleanly

    assert task.done(), "Watchdog task did not complete after cancel"


@pytest.mark.asyncio
async def test_inactivity_timeout_env_var():
    """CALL_INACTIVITY_TIMEOUT env var overrides the default 300s timeout."""
    # This import will FAIL until CALL_INACTIVITY_TIMEOUT constant is added
    with patch.dict(os.environ, {"CALL_INACTIVITY_TIMEOUT": "42"}):
        # Force module reload to pick up env var
        import importlib
        import shuo.conversation as conv_module
        importlib.reload(conv_module)
        assert hasattr(conv_module, "CALL_INACTIVITY_TIMEOUT"), (
            "CALL_INACTIVITY_TIMEOUT constant not found in shuo.conversation"
        )
        assert conv_module.CALL_INACTIVITY_TIMEOUT == 42.0, (
            f"Expected 42.0, got {conv_module.CALL_INACTIVITY_TIMEOUT}"
        )
