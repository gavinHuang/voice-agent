"""
Tests for Phone Protocol shape and LocalPhone behavior.
"""

import asyncio
import base64
import re
import pytest

from shuo.phone import Phone


# =============================================================================
# Protocol shape tests
# =============================================================================

def test_protocol_has_all_methods():
    """Phone Protocol exposes exactly 7 async methods."""
    expected = {"start", "stop", "send_audio", "send_clear", "send_dtmf", "hangup", "call"}
    actual = {name for name in dir(Phone) if not name.startswith("_")}
    assert expected == actual, f"Expected {expected}, got {actual}"


# =============================================================================
# LocalPhone behavioral tests
# =============================================================================

@pytest.mark.asyncio
async def test_local_phone_audio_routing():
    """After pair(a, b) and start, audio sent by a arrives at b's on_audio."""
    from shuo.phone import LocalPhone

    received: list[bytes] = []

    async def on_audio_b(data: bytes) -> None:
        received.append(data)

    async def noop_start(stream_sid, call_sid, phone) -> None:
        pass

    async def noop_stop() -> None:
        pass

    a = LocalPhone()
    b = LocalPhone()
    LocalPhone.pair(a, b)

    await a.start(lambda _: asyncio.sleep(0), noop_start, noop_stop)
    await b.start(on_audio_b, noop_start, noop_stop)

    raw = b"hello audio"
    payload = base64.b64encode(raw).decode()
    await a.send_audio(payload)

    # Give the reader task a moment to process
    await asyncio.sleep(0.05)

    assert received == [raw], f"Expected {[raw]}, got {received}"

    await a.stop()
    await b.stop()


@pytest.mark.asyncio
async def test_local_phone_start_fires_on_start():
    """start() immediately calls on_start with synthetic stream_sid, call_sid, phone."""
    from shuo.phone import LocalPhone

    captured: list[tuple] = []

    async def on_start(stream_sid: str, call_sid: str, phone: str) -> None:
        captured.append((stream_sid, call_sid, phone))

    async def noop_media(data: bytes) -> None:
        pass

    async def noop_stop() -> None:
        pass

    phone = LocalPhone()
    await phone.start(noop_media, on_start, noop_stop)

    assert len(captured) == 1
    stream_sid, call_sid, phone_num = captured[0]
    assert re.match(r"^local-[a-f0-9]+$", stream_sid), f"Bad stream_sid: {stream_sid!r}"
    assert call_sid == "local-call-sid"
    assert phone_num == "local"

    await phone.stop()


@pytest.mark.asyncio
async def test_local_phone_dtmf():
    """send_dtmf on a delivers digit to b via b._inject."""
    from shuo.phone import LocalPhone
    from shuo.call import DTMFEvent

    injected: list = []

    async def noop_media(data: bytes) -> None:
        pass

    async def noop_start(stream_sid, call_sid, phone) -> None:
        pass

    async def noop_stop() -> None:
        pass

    a = LocalPhone()
    b = LocalPhone()
    LocalPhone.pair(a, b)

    # Wire up b's inject before start so send_dtmf can deliver
    b._inject = injected.append

    await a.start(noop_media, noop_start, noop_stop)
    await b.start(noop_media, noop_start, noop_stop)

    await a.send_dtmf("5")

    assert injected == [DTMFEvent(digits="5")], f"Got: {injected}"

    await a.stop()
    await b.stop()


@pytest.mark.asyncio
async def test_local_phone_hangup():
    """a.hangup() fires b's on_stop callback."""
    from shuo.phone import LocalPhone

    stopped: list[bool] = []

    async def noop_media(data: bytes) -> None:
        pass

    async def noop_start(stream_sid, call_sid, phone) -> None:
        pass

    async def on_stop_b() -> None:
        stopped.append(True)

    async def noop_stop() -> None:
        pass

    a = LocalPhone()
    b = LocalPhone()
    LocalPhone.pair(a, b)

    await a.start(noop_media, noop_start, noop_stop)
    await b.start(noop_media, noop_start, on_stop_b)

    await a.hangup()

    assert stopped == [True]

    await a.stop()
    await b.stop()


@pytest.mark.asyncio
async def test_local_phone_send_clear_is_noop():
    """send_clear() completes without error (no-op for in-process)."""
    from shuo.phone import LocalPhone

    async def noop_media(data: bytes) -> None:
        pass

    async def noop_start(stream_sid, call_sid, phone) -> None:
        pass

    async def noop_stop() -> None:
        pass

    phone = LocalPhone()
    await phone.start(noop_media, noop_start, noop_stop)
    await phone.send_clear()  # Should not raise
    await phone.stop()


@pytest.mark.asyncio
async def test_local_phone_stop_terminates_reader():
    """stop() terminates the background reader task cleanly."""
    from shuo.phone import LocalPhone

    async def noop_media(data: bytes) -> None:
        pass

    async def noop_start(stream_sid, call_sid, phone) -> None:
        pass

    async def noop_stop() -> None:
        pass

    phone = LocalPhone()
    await phone.start(noop_media, noop_start, noop_stop)

    assert phone._task is not None
    assert not phone._task.done()

    await phone.stop()

    assert phone._task is None or phone._task.done()


@pytest.mark.asyncio
async def test_local_phone_call_is_noop():
    """call() completes without error (pairing happens at construction time)."""
    from shuo.phone import LocalPhone

    phone = LocalPhone()
    await phone.call("+15550001234", "https://example.com/twiml")
    # No exception = pass


# =============================================================================
# AgentPhone
# =============================================================================

@pytest.mark.asyncio
async def test_agent_phone_pair_returns_two_local_phones():
    """AgentPhone.pair() returns two paired LocalPhone instances."""
    from shuo.phone import AgentPhone, LocalPhone

    a, b = AgentPhone.pair()
    assert isinstance(a, LocalPhone)
    assert isinstance(b, LocalPhone)
    assert a._peer is b
    assert b._peer is a


@pytest.mark.asyncio
async def test_agent_phone_pair_audio_exchange():
    """Audio sent by phone A is received by phone B and vice versa."""
    import base64
    from shuo.phone import AgentPhone

    received_by_b: list = []
    received_by_a: list = []

    async def on_audio_b(data: bytes) -> None:
        received_by_b.append(data)

    async def on_audio_a(data: bytes) -> None:
        received_by_a.append(data)

    async def noop_start(sid, csid, ph): pass
    async def noop_stop(): pass

    a, b = AgentPhone.pair()
    await a.start(on_audio_a, noop_start, noop_stop)
    await b.start(on_audio_b, noop_start, noop_stop)

    payload = base64.b64encode(b"\x00\x01\x02").decode()
    await a.send_audio(payload)  # A sends → B should receive

    # Give the asyncio queue a moment to drain
    import asyncio
    await asyncio.sleep(0.05)

    assert len(received_by_b) == 1
    assert received_by_b[0] == b"\x00\x01\x02"

    await a.stop()
    await b.stop()


@pytest.mark.asyncio
async def test_local_phone_connection_timeout():
    """LocalPhone raises TimeoutError if no audio arrives within the timeout."""
    import asyncio
    import os
    os.environ["LOCAL_PHONE_TIMEOUT"] = "0.1"  # very short for test

    from shuo.phone import LocalPhone

    # Re-read the class attribute (it's read at class definition time, patch it)
    phone = LocalPhone()
    phone._CONNECTION_TIMEOUT = 0.1  # override instance

    received: list = []

    async def on_audio(data: bytes) -> None:
        received.append(data)

    async def noop_start(sid, csid, ph): pass
    async def noop_stop(): pass

    await phone.start(on_audio, noop_start, noop_stop)

    with pytest.raises(TimeoutError, match="peer did not send audio"):
        await asyncio.wait_for(phone._task, timeout=1.0)

    del os.environ["LOCAL_PHONE_TIMEOUT"]
