"""
Flux (Deepgram) connection pool.

Pre-initialises Deepgram WebSocket connections and manages their lifecycle.
Deepgram connections take ~1.4 s to establish; pre-warming eliminates that
latency from every call.

Usage:
    pool = FluxPool(pool_size=1, ttl=120.0)
    await pool.start()

    flux = await pool.get(on_end_of_turn=..., on_start_of_turn=...)
    # flux is connected and ready immediately

    await pool.stop()
"""

import asyncio
import time
from typing import Optional, Callable, Awaitable, List
from dataclasses import dataclass

from .flux import FluxService
from ..log import ServiceLogger

log = ServiceLogger("FluxPool")


async def _noop_end_of_turn(_text: str) -> None:
    pass


async def _noop_start_of_turn() -> None:
    pass


@dataclass
class _Entry:
    flux: FluxService
    created_at: float  # time.monotonic()


class FluxPool:
    """
    Connection pool for Deepgram Flux WebSockets.

    - Pre-connects `pool_size` connections at startup
    - Dispenses warm connections via get() with callback rebinding
    - Evicts connections older than `ttl` seconds
    - Auto-refills in the background after dispensing or eviction
    """

    def __init__(self, pool_size: int = 1, ttl: float = 120.0):
        self._pool_size = pool_size
        self._ttl = ttl

        self._ready: List[_Entry] = []
        self._running = False
        self._fill_event = asyncio.Event()
        self._fill_task: Optional[asyncio.Task] = None

    @property
    def available(self) -> int:
        return len(self._ready)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._fill_task = asyncio.create_task(self._fill_loop())

    async def get(
        self,
        on_end_of_turn: Callable[[str], Awaitable[None]],
        on_start_of_turn: Callable[[], Awaitable[None]],
    ) -> FluxService:
        """
        Get a connected Flux service with the given callbacks.

        Returns a warm connection if available (and not stale),
        otherwise blocks to create a fresh one.
        """
        while self._ready:
            entry = self._ready.pop(0)
            age = time.monotonic() - entry.created_at

            if age < self._ttl:
                entry.flux.bind(on_end_of_turn, on_start_of_turn)
                log.info(f"Dispensed warm connection (idle {int(age * 1000)}ms)")
                self._trigger_fill()
                return entry.flux
            else:
                log.info(f"Discarded stale connection (idle {int(age * 1000)}ms)")
                await entry.flux.stop()

        log.info("Pool empty, connecting fresh...")
        flux = FluxService(on_end_of_turn=on_end_of_turn, on_start_of_turn=on_start_of_turn)
        await flux.start()
        self._trigger_fill()
        return flux

    async def stop(self) -> None:
        self._running = False
        self._fill_event.set()

        if self._fill_task:
            self._fill_task.cancel()
            try:
                await self._fill_task
            except asyncio.CancelledError:
                pass
            self._fill_task = None

        for entry in self._ready:
            await entry.flux.stop()
        self._ready.clear()

    def _trigger_fill(self) -> None:
        self._fill_event.set()

    async def _fill_loop(self) -> None:
        try:
            while self._running:
                await self._evict_stale()

                while self._running and len(self._ready) < self._pool_size:
                    flux = FluxService(
                        on_end_of_turn=_noop_end_of_turn,
                        on_start_of_turn=_noop_start_of_turn,
                    )
                    try:
                        await flux.start()
                        self._ready.append(
                            _Entry(flux=flux, created_at=time.monotonic())
                        )
                        log.info(
                            f"🔥 Warm connection ready "
                            f"({len(self._ready)}/{self._pool_size})"
                        )
                    except Exception as e:
                        log.error("Pre-connect failed", e)
                        await asyncio.sleep(2.0)

                self._fill_event.clear()
                try:
                    await asyncio.wait_for(
                        self._fill_event.wait(),
                        timeout=self._ttl / 2,
                    )
                except asyncio.TimeoutError:
                    pass

        except asyncio.CancelledError:
            pass

    async def _evict_stale(self) -> None:
        now = time.monotonic()
        fresh: List[_Entry] = []

        for entry in self._ready:
            age = now - entry.created_at
            if age < self._ttl:
                fresh.append(entry)
            else:
                log.info(f"Evicted stale connection (idle {int(age * 1000)}ms)")
                await entry.flux.stop()

        self._ready = fresh
