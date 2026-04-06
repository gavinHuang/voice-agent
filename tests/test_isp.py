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
