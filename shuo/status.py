"""
status.py — System status checker with background probing and history.

Probes all critical components (Twilio, Deepgram, LLM, TTS, Supabase)
and external status pages. Maintains an in-memory ring buffer of recent
check results for trend visibility.
"""

import asyncio
import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from .log import get_logger

logger = get_logger("shuo.status")

# ── Status classification ─────────────────────────────────────────────

DEGRADED_THRESHOLD_MS = 2000


@dataclass
class ComponentStatus:
    name: str
    status: str = "unknown"  # healthy | degraded | down | unknown | warming_up
    response_time_ms: Optional[float] = None
    error: Optional[str] = None
    external_status: Optional[str] = None  # operational | incident | unknown
    external_description: Optional[str] = None
    details: dict = field(default_factory=dict)


def _classify(response_time_ms: float) -> str:
    if response_time_ms > DEGRADED_THRESHOLD_MS:
        return "degraded"
    return "healthy"


def _mask_secret(value: str) -> str:
    if not value or len(value) <= 4:
        return "****"
    return f"****{value[-4:]}"


# ── Individual probes ─────────────────────────────────────────────────

async def _probe_twilio() -> ComponentStatus:
    cs = ComponentStatus(name="twilio")
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    if not sid or not token:
        cs.status = "unknown"
        cs.error = "TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN not configured"
        return cs
    try:
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
                auth=(sid, token),
            )
        elapsed = (time.monotonic() - t0) * 1000
        cs.response_time_ms = round(elapsed, 1)
        if resp.status_code == 200:
            cs.status = _classify(elapsed)
        else:
            cs.status = "down"
            cs.error = f"HTTP {resp.status_code}"
    except Exception as e:
        cs.status = "down"
        cs.error = str(e)
    return cs


async def _probe_deepgram() -> ComponentStatus:
    cs = ComponentStatus(name="deepgram")
    api_key = os.getenv("DEEPGRAM_API_KEY", "")
    if not api_key:
        cs.status = "unknown"
        cs.error = "DEEPGRAM_API_KEY not configured"
        return cs
    try:
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://api.deepgram.com/v1/projects",
                headers={"Authorization": f"Token {api_key}"},
            )
        elapsed = (time.monotonic() - t0) * 1000
        cs.response_time_ms = round(elapsed, 1)
        if resp.status_code == 200:
            cs.status = _classify(elapsed)
        else:
            cs.status = "down"
            cs.error = f"HTTP {resp.status_code}"
    except Exception as e:
        cs.status = "down"
        cs.error = str(e)
    return cs


async def _probe_llm() -> ComponentStatus:
    cs = ComponentStatus(name="llm")
    groq_key = os.getenv("GROQ_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")
    model = os.getenv("LLM_MODEL", "")

    if groq_key:
        provider = "groq"
        url = "https://api.groq.com/openai/v1/models"
        headers = {"Authorization": f"Bearer {groq_key}"}
    elif openai_key:
        provider = "openai"
        url = "https://api.openai.com/v1/models"
        headers = {"Authorization": f"Bearer {openai_key}"}
    else:
        cs.status = "unknown"
        cs.error = "No LLM API key configured (GROQ_API_KEY or OPENAI_API_KEY)"
        return cs

    cs.details["provider"] = provider
    if model:
        cs.details["model"] = model

    try:
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
        elapsed = (time.monotonic() - t0) * 1000
        cs.response_time_ms = round(elapsed, 1)
        if resp.status_code == 200:
            cs.status = _classify(elapsed)
        else:
            cs.status = "down"
            cs.error = f"HTTP {resp.status_code}"
    except Exception as e:
        cs.status = "down"
        cs.error = str(e)
    return cs


async def _probe_tts(voice_pool) -> ComponentStatus:
    cs = ComponentStatus(name="tts")
    provider = os.getenv("TTS_PROVIDER", "kokoro").lower()
    cs.details["provider"] = provider

    if provider in ("kokoro", "vibevoice"):
        # Local TTS — check if voice pool has active connections
        if voice_pool is None:
            cs.status = "unknown"
            cs.error = "Voice pool not initialized"
        elif voice_pool.available > 0:
            cs.status = "healthy"
            cs.details["pool_available"] = voice_pool.available
        else:
            cs.status = "degraded"
            cs.details["pool_available"] = 0
            cs.error = "No warm connections in voice pool"
    elif provider == "elevenlabs":
        api_key = os.getenv("ELEVENLABS_API_KEY", "")
        if not api_key:
            cs.status = "unknown"
            cs.error = "ELEVENLABS_API_KEY not configured"
        else:
            try:
                t0 = time.monotonic()
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(
                        "https://api.elevenlabs.io/v1/voices",
                        headers={"xi-api-key": api_key},
                    )
                elapsed = (time.monotonic() - t0) * 1000
                cs.response_time_ms = round(elapsed, 1)
                if resp.status_code == 200:
                    cs.status = _classify(elapsed)
                else:
                    cs.status = "down"
                    cs.error = f"HTTP {resp.status_code}"
            except Exception as e:
                cs.status = "down"
                cs.error = str(e)
    elif provider == "fish":
        # Fish Audio — self-hosted, no standard health endpoint
        cs.status = "unknown"
        cs.details["note"] = "Self-hosted; no health probe available"
    else:
        cs.status = "unknown"
        cs.error = f"Unknown TTS provider: {provider}"

    return cs


async def _probe_supabase() -> ComponentStatus:
    cs = ComponentStatus(name="supabase")
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY", "")
    if not url:
        cs.status = "unknown"
        cs.error = "SUPABASE_URL not configured"
        return cs
    try:
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=5.0) as client:
            # PostgREST root returns version info
            resp = await client.get(
                f"{url.rstrip('/')}/rest/v1/",
                headers={"apikey": key} if key else {},
            )
        elapsed = (time.monotonic() - t0) * 1000
        cs.response_time_ms = round(elapsed, 1)
        if resp.status_code in (200, 301, 404):
            # 200 or redirect = Supabase is reachable
            cs.status = _classify(elapsed)
        else:
            cs.status = "down"
            cs.error = f"HTTP {resp.status_code}"
    except Exception as e:
        cs.status = "down"
        cs.error = str(e)
    return cs


async def _probe_callback_url() -> ComponentStatus:
    """Verify TWILIO_PUBLIC_URL actually routes back to this server.

    This catches the common failure mode where ngrok restarts and the URL
    goes stale, or the URL is misconfigured — Twilio can't reach us even
    though we can reach Twilio.
    """
    cs = ComponentStatus(name="callback_url")
    public_url = os.getenv("TWILIO_PUBLIC_URL", "")
    if not public_url:
        cs.status = "down"
        cs.error = "TWILIO_PUBLIC_URL not set — Twilio cannot reach this server"
        return cs

    cs.details["twilio_public_url"] = public_url

    # Basic format checks
    if not public_url.startswith("https://"):
        cs.status = "down"
        cs.error = f"TWILIO_PUBLIC_URL must be HTTPS (got {public_url[:40]})"
        return cs
    if "localhost" in public_url or "127.0.0.1" in public_url:
        cs.status = "down"
        cs.error = "TWILIO_PUBLIC_URL points to localhost — unreachable by Twilio"
        return cs

    # Self-check: hit our own /health through the public URL
    try:
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(f"{public_url.rstrip('/')}/health")
        elapsed = (time.monotonic() - t0) * 1000
        cs.response_time_ms = round(elapsed, 1)
        if resp.status_code == 200:
            body = resp.json()
            if body.get("status") == "ok":
                cs.status = _classify(elapsed)
            else:
                cs.status = "down"
                cs.error = (
                    f"URL reachable but /health returned unexpected body — "
                    f"may be pointing at wrong server"
                )
        else:
            cs.status = "down"
            cs.error = f"Public URL returned HTTP {resp.status_code} — tunnel may be dead"
    except httpx.ConnectError:
        cs.status = "down"
        cs.error = "Cannot connect to TWILIO_PUBLIC_URL — ngrok tunnel down or URL stale"
    except httpx.TimeoutException:
        cs.status = "down"
        cs.error = "TWILIO_PUBLIC_URL timed out — tunnel may be dead"
    except Exception as e:
        cs.status = "down"
        cs.error = f"Self-check failed: {e}"
    return cs


async def _probe_twilio_webhook() -> ComponentStatus:
    """Check if the Twilio phone number's voice webhook URL matches our public URL.

    Catches the case where calls will fail because the phone number's webhook
    still points to an old/different server URL.
    """
    cs = ComponentStatus(name="twilio_webhook")
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    phone_number = os.getenv("TWILIO_PHONE_NUMBER", "")
    public_url = os.getenv("TWILIO_PUBLIC_URL", "")

    if not sid or not token:
        cs.status = "unknown"
        cs.error = "Twilio credentials not configured"
        return cs
    if not phone_number:
        cs.status = "unknown"
        cs.error = "TWILIO_PHONE_NUMBER not set"
        return cs

    expected_voice_url = f"{public_url.rstrip('/')}/twiml" if public_url else ""
    cs.details["expected_voice_url"] = expected_voice_url
    cs.details["phone_number"] = phone_number

    try:
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=8.0) as client:
            # Search for incoming phone numbers matching our configured number
            resp = await client.get(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}"
                f"/IncomingPhoneNumbers.json?PhoneNumber={phone_number}",
                auth=(sid, token),
            )
        elapsed = (time.monotonic() - t0) * 1000
        cs.response_time_ms = round(elapsed, 1)

        if resp.status_code != 200:
            cs.status = "down"
            cs.error = f"Could not fetch phone number config from Twilio (HTTP {resp.status_code})"
            return cs

        data = resp.json()
        numbers = data.get("incoming_phone_numbers", [])
        if not numbers:
            cs.status = "down"
            cs.error = f"Phone number {phone_number} not found in Twilio account"
            return cs

        actual_voice_url = numbers[0].get("voice_url", "")
        cs.details["actual_voice_url"] = actual_voice_url

        if not expected_voice_url:
            cs.status = "down"
            cs.error = "TWILIO_PUBLIC_URL not set — cannot verify webhook"
        elif actual_voice_url == expected_voice_url:
            cs.status = "healthy"
        elif actual_voice_url and expected_voice_url:
            # URL mismatch — this means inbound calls will go to the wrong place
            cs.status = "down"
            cs.error = (
                f"Webhook URL mismatch! "
                f"Twilio will send calls to {actual_voice_url} "
                f"but this server expects {expected_voice_url}"
            )
        else:
            cs.status = "down"
            cs.error = f"No voice URL configured on Twilio phone number"
    except Exception as e:
        cs.status = "down"
        cs.error = f"Webhook check failed: {e}"
    return cs


# ── External status page probes ──────────────────────────────────────

async def _fetch_external_status_twilio() -> tuple[str, Optional[str]]:
    """Fetch Twilio's external status from their status page API."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("https://gpkpyklzq6tu.statuspage.io/api/v2/status.json")
        if resp.status_code == 200:
            data = resp.json()
            indicator = data.get("status", {}).get("indicator", "")
            desc = data.get("status", {}).get("description", "")
            if indicator == "none":
                return "operational", None
            return "incident", desc
    except Exception:
        pass
    return "unknown", None


async def _fetch_external_status_deepgram() -> tuple[str, Optional[str]]:
    """Fetch Deepgram's external status from their status page API."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("https://status.deepgram.com/api/v2/status.json")
        if resp.status_code == 200:
            data = resp.json()
            indicator = data.get("status", {}).get("indicator", "")
            desc = data.get("status", {}).get("description", "")
            if indicator == "none":
                return "operational", None
            return "incident", desc
    except Exception:
        pass
    return "unknown", None


# ── Config reporter ───────────────────────────────────────────────────

def _collect_config() -> dict:
    """Collect active configuration settings with masked secrets."""
    config = {}

    # ── Critical connectivity settings (shown prominently) ───────────
    config["twilio_public_url"] = os.getenv("TWILIO_PUBLIC_URL", "not set")
    config["twilio_phone_number"] = os.getenv("TWILIO_PHONE_NUMBER", "not set")

    # TTS
    config["tts_provider"] = os.getenv("TTS_PROVIDER", "kokoro")
    if config["tts_provider"] == "elevenlabs":
        config["elevenlabs_voice_id"] = os.getenv("ELEVENLABS_VOICE_ID", "default")
        config["elevenlabs_model"] = os.getenv("ELEVENLABS_MODEL", "default")
    elif config["tts_provider"] == "kokoro":
        config["kokoro_voice"] = os.getenv("KOKORO_VOICE", "default")
        config["kokoro_lang"] = os.getenv("KOKORO_LANG", "default")

    # LLM
    config["llm_model"] = os.getenv("LLM_MODEL", "groq:openai/gpt-oss-120b")

    # STT
    config["stt_model"] = os.getenv("DEEPGRAM_MODEL", "nova-2")
    config["deepgram_language"] = os.getenv("DEEPGRAM_LANGUAGE", "en")
    config["deepgram_region"] = os.getenv("DEEPGRAM_REGION", "default")

    # Translation
    caller_lang = os.getenv("CALLER_LANG", "")
    callee_lang = os.getenv("CALLEE_LANG", "")
    config["translation_enabled"] = bool(caller_lang and callee_lang)
    if caller_lang:
        config["caller_lang"] = caller_lang
    if callee_lang:
        config["callee_lang"] = callee_lang
    config["translation_provider"] = os.getenv("TRANSLATION_PROVIDER", "llm")

    # Server
    config["port"] = os.getenv("PORT", "3040")
    config["cors_origins"] = os.getenv("CORS_ORIGINS", "*")

    # Supabase (may be configured on web side, but surface here if set)
    supabase_url = os.getenv("SUPABASE_URL", "")
    config["supabase_url"] = supabase_url if supabase_url else "not set"

    # Multi-tenant
    tenants_yaml = os.getenv("TENANTS_YAML", "")
    config["tenants_yaml"] = tenants_yaml if tenants_yaml else "not set"

    # Masked secrets — show presence only
    for key in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "DEEPGRAM_API_KEY",
                "GROQ_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY",
                "SUPABASE_KEY", "INTERNAL_API_KEY", "DASHBOARD_API_KEY",
                "NGROK_AUTHTOKEN"):
        val = os.getenv(key, "")
        config[key.lower()] = _mask_secret(val) if val else "not set"

    return config


# ── StatusChecker class ───────────────────────────────────────────────

class StatusChecker:
    """Runs periodic background probes and maintains a ring buffer of results."""

    def __init__(self, interval: float = 30.0, history_size: int = 60):
        self._interval = interval
        self._history: deque = deque(maxlen=history_size)
        self._latest: Optional[dict] = None
        self._task: Optional[asyncio.Task] = None
        self._voice_pool = None
        self._start_time = time.monotonic()
        self._external_cache: dict = {}
        self._external_cache_time: float = 0
        self._external_cache_ttl: float = 300  # 5 minutes

    def set_voice_pool(self, pool):
        self._voice_pool = pool

    async def start(self):
        self._start_time = time.monotonic()
        self._task = asyncio.create_task(self._loop())
        logger.info("StatusChecker started")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("StatusChecker stopped")

    def get_latest(self) -> Optional[dict]:
        return self._latest

    def get_history(self) -> list:
        return list(self._history)

    async def _loop(self):
        while True:
            try:
                await self._run_probes()
            except Exception as e:
                logger.error(f"StatusChecker probe error: {e}")
            await asyncio.sleep(self._interval)

    async def _run_probes(self):
        # Run all probes concurrently
        twilio, deepgram, llm, tts, supabase, callback_url, twilio_webhook = (
            await asyncio.gather(
                _probe_twilio(),
                _probe_deepgram(),
                _probe_llm(),
                _probe_tts(self._voice_pool),
                _probe_supabase(),
                _probe_callback_url(),
                _probe_twilio_webhook(),
            )
        )

        # External status pages (cached for 5 minutes)
        now = time.monotonic()
        if now - self._external_cache_time > self._external_cache_ttl:
            ext_twilio, ext_deepgram = await asyncio.gather(
                _fetch_external_status_twilio(),
                _fetch_external_status_deepgram(),
            )
            self._external_cache = {
                "twilio": ext_twilio,
                "deepgram": ext_deepgram,
            }
            self._external_cache_time = now

        # Attach external status
        ext_tw = self._external_cache.get("twilio", ("unknown", None))
        twilio.external_status = ext_tw[0]
        twilio.external_description = ext_tw[1]

        ext_dg = self._external_cache.get("deepgram", ("unknown", None))
        deepgram.external_status = ext_dg[0]
        deepgram.external_description = ext_dg[1]

        components = [twilio, deepgram, llm, tts, supabase, callback_url, twilio_webhook]

        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": round(time.monotonic() - self._start_time),
            "components": {
                c.name: {
                    "status": c.status,
                    "response_time_ms": c.response_time_ms,
                    "error": c.error,
                    "external_status": c.external_status,
                    "external_description": c.external_description,
                    "details": c.details,
                }
                for c in components
            },
            "config": _collect_config(),
        }

        self._latest = snapshot
        self._history.append({
            "timestamp": snapshot["timestamp"],
            "components": {
                c.name: c.status for c in components
            },
        })

    def get_warmup_response(self) -> dict:
        """Return a status response indicating all components are warming up."""
        component_names = ["twilio", "deepgram", "llm", "tts", "supabase",
                           "callback_url", "twilio_webhook"]
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": round(time.monotonic() - self._start_time),
            "components": {
                name: {
                    "status": "warming_up",
                    "response_time_ms": None,
                    "error": None,
                    "external_status": None,
                    "external_description": None,
                    "details": {},
                }
                for name in component_names
            },
            "config": _collect_config(),
            "history": [],
        }
