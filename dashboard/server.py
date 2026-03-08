"""
Dashboard API — monitoring and control endpoints for live calls.

Routes (all under /dashboard):
    GET  /             — serve dashboard UI (app.html)
    WS   /ws           — stream all call events to supervisor browser
    GET  /calls        — list active calls (for initial page load)
    POST /calls/{id}/hangup    — terminate call via Twilio REST
    POST /calls/{id}/takeover  — suppress agent, human takes over
    POST /calls/{id}/handback  — return control to agent
    POST /calls/{id}/dtmf      — inject a DTMF digit into call audio
"""

import os
import time
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import bus as dashboard_bus
from . import registry

_HERE = Path(__file__).parent

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ── Pages ────────────────────────────────────────────────────────────────────

@router.get("/")
async def dashboard_page():
    """Serve the supervisor dashboard UI."""
    return FileResponse(_HERE / "app.html")


# ── WebSocket event stream ────────────────────────────────────────────────────

@router.websocket("/ws")
async def dashboard_ws(websocket: WebSocket):
    """
    Push all call events to a connected supervisor browser.

    On connect: sends current active call state.
    Ongoing: forwards every event from every call (tagged with call_id).
    """
    await websocket.accept()
    q = dashboard_bus.subscribe_global()

    # Send current state so the page can render existing calls immediately
    calls_snapshot = [
        {
            "call_id":  c.call_id,
            "phone":    c.phone,
            "goal":     c.goal,
            "mode":     c.mode.value,
            "elapsed":  int(time.monotonic() - c.started_at),
        }
        for c in registry.all_calls()
    ]
    await websocket.send_json({"type": "active_calls", "calls": calls_snapshot})

    try:
        while True:
            event = await q.get()
            await websocket.send_json(event)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        dashboard_bus.unsubscribe_global(q)


# ── Call list ─────────────────────────────────────────────────────────────────

@router.get("/calls")
async def list_calls():
    """Return all active calls as JSON."""
    return JSONResponse({
        "calls": [
            {
                "call_id": c.call_id,
                "phone":   c.phone,
                "goal":    c.goal,
                "mode":    c.mode.value,
            }
            for c in registry.all_calls()
        ]
    })


# ── Control: hang up ──────────────────────────────────────────────────────────

@router.post("/calls/{call_id}/hangup")
async def hangup(call_id: str):
    """Terminate the call via Twilio REST API."""
    call = registry.get(call_id)
    if not call:
        return JSONResponse({"error": "Call not found"}, status_code=404)

    if not call.call_sid:
        return JSONResponse({"error": "Call SID not yet available"}, status_code=503)

    try:
        from twilio.rest import Client
        client = Client(
            os.getenv("TWILIO_ACCOUNT_SID"),
            os.getenv("TWILIO_AUTH_TOKEN"),
        )
        client.calls(call.call_sid).update(status="completed")
        return {"status": "ok"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Control: takeover / handback ─────────────────────────────────────────────

@router.post("/calls/{call_id}/takeover")
async def takeover(call_id: str):
    """
    Suppress the agent and hand control to the human supervisor.

    The agent will finish its current utterance (if any) then stop responding.
    The supervisor uses the softphone (/phone) to speak to the caller.
    """
    call = registry.get(call_id)
    if not call:
        return JSONResponse({"error": "Call not found"}, status_code=404)

    registry.update(call_id, mode=registry.CallMode.TAKEOVER)
    dashboard_bus.publish_global({"call_id": call_id, "type": "takeover"})
    return {"status": "ok"}


@router.post("/calls/{call_id}/handback")
async def handback(call_id: str):
    """Return control to the agent."""
    call = registry.get(call_id)
    if not call:
        return JSONResponse({"error": "Call not found"}, status_code=404)

    registry.update(call_id, mode=registry.CallMode.AGENT)
    dashboard_bus.publish_global({"call_id": call_id, "type": "handback"})
    return {"status": "ok"}


# ── Control: DTMF injection ───────────────────────────────────────────────────

class DTMFRequest(BaseModel):
    digit: str


@router.post("/calls/{call_id}/dtmf")
async def inject_dtmf(call_id: str, body: DTMFRequest):
    """
    Inject a DTMF tone into the call audio stream.

    Generates the tone as μ-law audio and sends it through the live
    Twilio WebSocket — identical to how the agent sends DTMF tones.
    """
    call = registry.get(call_id)
    if not call:
        return JSONResponse({"error": "Call not found"}, status_code=404)

    digit = body.digit
    if digit not in "0123456789*#":
        return JSONResponse({"error": f"Invalid DTMF digit: {digit!r}"}, status_code=400)

    if not call.agent:
        return JSONResponse({"error": "Agent not ready"}, status_code=503)

    try:
        await call.agent.inject_dtmf(digit)
        dashboard_bus.publish_global({"call_id": call_id, "type": "dtmf", "digit": digit})
        return {"status": "ok"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
