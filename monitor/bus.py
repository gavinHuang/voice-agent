"""
In-process event bus for the monitoring dashboard.

Each active call gets a CallBus. All dashboard WS clients subscribe to the
global queue and receive events from every call, tagged with call_id.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class CallBus:
    call_id: str
    _queues: List[asyncio.Queue] = field(default_factory=list)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    def publish(self, event: dict) -> None:
        for q in list(self._queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


# Per-call buses
_buses: Dict[str, CallBus] = {}

# Global subscribers — dashboard WS clients watching all calls
_global_queues: List[asyncio.Queue] = []


def create(call_id: str) -> CallBus:
    bus = CallBus(call_id=call_id)
    _buses[call_id] = bus
    return bus


def get(call_id: str) -> Optional[CallBus]:
    return _buses.get(call_id)


def destroy(call_id: str) -> None:
    _buses.pop(call_id, None)


def subscribe_global() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=512)
    _global_queues.append(q)
    return q


def unsubscribe_global(q: asyncio.Queue) -> None:
    try:
        _global_queues.remove(q)
    except ValueError:
        pass


def publish_global(event: dict) -> None:
    """Broadcast an event to all connected dashboard WebSocket clients."""
    for q in list(_global_queues):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
