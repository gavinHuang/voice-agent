"""
ElevenLabs TTS via WebSocket streaming.

Env vars:
    ELEVENLABS_API_KEY  — API key
    ELEVENLABS_VOICE_ID — voice ID (default: Rachel)
    ELEVENLABS_MODEL    — model ID (default: eleven_flash_v2_5)
"""

import os
import json
import asyncio
from typing import Optional, Callable, Awaitable

import websockets
from websockets.client import WebSocketClientProtocol

from ..log import ServiceLogger

log = ServiceLogger("TTS")


class ElevenLabsTTS:
    """ElevenLabs streaming TTS — WebSocket → μ-law 8 kHz base64 for Twilio."""

    def __init__(
        self,
        on_audio: Callable[[str], Awaitable[None]],
        on_done: Callable[[], Awaitable[None]],
    ):
        self._on_audio = on_audio
        self._on_done = on_done

        self._ws: Optional[WebSocketClientProtocol] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._running = False

        self._api_key = os.getenv("ELEVENLABS_API_KEY", "")
        self._voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
        self._model_id = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")

    @property
    def is_active(self) -> bool:
        return self._running and self._ws is not None

    def bind(
        self,
        on_audio: Callable[[str], Awaitable[None]],
        on_done: Callable[[], Awaitable[None]],
    ) -> None:
        self._on_audio = on_audio
        self._on_done = on_done

    async def start(self) -> None:
        if self._running:
            return
        url = (
            f"wss://api.elevenlabs.io/v1/text-to-speech/{self._voice_id}/stream-input"
            f"?model_id={self._model_id}&output_format=ulaw_8000"
        )
        try:
            self._ws = await websockets.connect(url)
            self._running = True
            resp = getattr(self._ws, "response", None)
            hdrs = getattr(resp, "headers", {}) if resp else {}
            log.info(f"Region: {hdrs.get('x-region', 'unknown')}")
            await self._ws.send(json.dumps({
                "text": " ",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                "xi_api_key": self._api_key,
            }))
            self._receive_task = asyncio.create_task(self._receive_loop())
            log.connected()
        except Exception as e:
            log.error("Connection failed", e)
            raise

    async def send(self, text: str) -> None:
        if not self._ws or not self._running:
            return
        try:
            await self._ws.send(json.dumps({"text": text, "try_trigger_generation": True}))
        except Exception as e:
            log.error("Send failed", e)

    async def flush(self) -> None:
        if not self._ws or not self._running:
            return
        try:
            await self._ws.send(json.dumps({"text": "", "flush": True}))
        except Exception as e:
            log.error("Flush failed", e)

    async def stop(self) -> None:
        if not self._running:
            return
        try:
            await self.flush()
            await asyncio.sleep(0.2)
        except Exception as e:
            log.error("Stop failed", e)
        finally:
            await self._cleanup()
        log.disconnected()

    async def cancel(self) -> None:
        self._running = False
        await self._cleanup()
        log.cancelled()

    async def _cleanup(self) -> None:
        self._running = False
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def _receive_loop(self) -> None:
        try:
            while self._running and self._ws:
                try:
                    message = await self._ws.recv()
                    await self._handle_message(message)
                except websockets.exceptions.ConnectionClosed:
                    break
                except Exception as e:
                    log.error("Receive failed", e)
                    break
        finally:
            if self._running:
                self._running = False
                await self._on_done()

    async def _handle_message(self, message: str) -> None:
        try:
            data = json.loads(message)
            if "error" in data or "detail" in data:
                log.error(f"ElevenLabs error: {data.get('error') or data.get('detail')}")
                return
            if "audio" in data and data["audio"]:
                await self._on_audio(data["audio"])
            if data.get("isFinal", False):
                await self._on_done()
        except json.JSONDecodeError:
            log.error(f"Invalid JSON: {message[:100]}")
