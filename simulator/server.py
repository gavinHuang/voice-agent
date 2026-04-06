"""
IVR mock server.

Endpoints:
    POST /twiml                -- Twilio entry point (start node)
    POST /ivr/step?node=ID     -- Render a node
    POST /ivr/gather?node=ID   -- Handle DTMF from a menu
    GET  /ivr/token            -- Twilio Access Token for browser softphone
    GET  /phone                -- Browser softphone UI
    GET  /health               -- Health check

Environment variables:
    IVR_CONFIG        Path to YAML flow config (default: flows/example.yaml)
    IVR_BASE_URL      Public base URL (e.g. https://xxxx.ngrok.io)
    TWILIO_ACCOUNT_SID
    TWILIO_AUTH_TOKEN
    TWILIO_TWIML_APP_SID   TwiML App SID for browser SDK
    TWILIO_CALLER_ID       Outbound caller ID
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from .config import load_config, IVRConfig
from .engine import TwiMLEngine

app = FastAPI(title="IVR Mock")

_config: Optional[IVRConfig] = None
_engine: Optional[TwiMLEngine] = None


def _get_engine() -> TwiMLEngine:
    global _config, _engine
    if _engine is None:
        config_path = os.getenv("IVR_CONFIG", str(Path(__file__).parent / "flows" / "example.yaml"))
        base_url = os.getenv("IVR_BASE_URL", "")
        _config = load_config(config_path)
        _engine = TwiMLEngine(_config, base_url=base_url)
    return _engine


# ── TwiML Endpoints ────────────────────────────────────────────────────────


@app.post("/twiml")
async def twiml_entry():
    """Entry point — Twilio calls this when a call arrives."""
    engine = _get_engine()
    return PlainTextResponse(engine.render_entry(), media_type="application/xml")


@app.post("/ivr/step")
async def ivr_step(node: str = Query(...)):
    """Render a node."""
    engine = _get_engine()
    try:
        xml = engine.render_node(node)
    except KeyError as e:
        return PlainTextResponse(
            '<?xml version="1.0"?><Response><Say>Configuration error.</Say><Hangup/></Response>',
            media_type="application/xml",
            status_code=200,
        )
    return PlainTextResponse(xml, media_type="application/xml")


@app.post("/ivr/gather")
async def ivr_gather(request: Request, node: str = Query(...)):
    """Handle DTMF input."""
    form = await request.form()
    digits = form.get("Digits", "")
    engine = _get_engine()
    try:
        xml = engine.render_gather(node, digits)
    except (KeyError, ValueError):
        xml = '<?xml version="1.0"?><Response><Say>Configuration error.</Say><Hangup/></Response>'
    return PlainTextResponse(xml, media_type="application/xml")


# ── Softphone / Token ──────────────────────────────────────────────────────


@app.get("/ivr/token")
async def ivr_token():
    """Generate Twilio Access Token for browser softphone."""
    try:
        from twilio.jwt.access_token import AccessToken
        from twilio.jwt.access_token.grants import VoiceGrant

        account_sid = os.environ["TWILIO_ACCOUNT_SID"]
        auth_token = os.environ["TWILIO_AUTH_TOKEN"]
        twiml_app_sid = os.environ["TWILIO_TWIML_APP_SID"]

        token = AccessToken(account_sid, auth_token, identity="browser")
        grant = VoiceGrant(outgoing_application_sid=twiml_app_sid, incoming_allow=True)
        token.add_grant(grant)

        return JSONResponse({"token": token.to_jwt()})
    except KeyError as e:
        return JSONResponse({"error": f"Missing env var: {e}"}, status_code=500)
    except ImportError:
        return JSONResponse({"error": "twilio package not installed"}, status_code=500)


@app.get("/phone", response_class=HTMLResponse)
async def phone_ui():
    """Serve the browser softphone UI."""
    html_path = Path(__file__).parent / "phone.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<html><body><p>phone.html not found</p></body></html>", status_code=404)


# ── Health ─────────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Config reload (test helper) ────────────────────────────────────────────


def reload_config(config_yaml: str) -> None:
    """
    Reload the IVR engine from a YAML string.
    Used in tests to inject an in-memory config.
    """
    import yaml
    from .config import parse_config

    global _config, _engine
    data = yaml.safe_load(config_yaml)
    _config = parse_config(data)
    base_url = os.getenv("IVR_BASE_URL", "")
    _engine = TwiMLEngine(_config, base_url=base_url)
