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
from typing import List

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

class CallRequest(BaseModel):
    phone: str
    goal: str = ""


@router.post("/call")
async def start_call(body: CallRequest):
    """
    Trigger an outbound call from the dashboard UI.

    Stores the goal keyed by call SID so the agent picks it up when the
    WebSocket stream_start event arrives.
    """
    phone = body.phone.strip()
    if not phone.startswith("+") and not phone.startswith("client:"):
        phone = f"+{phone}"

    try:
        from shuo.services.twilio_client import make_outbound_call
        call_sid = make_outbound_call(phone)
        registry.set_pending(call_sid, phone=phone, goal=body.goal)
        return {"status": "calling", "to": phone, "call_sid": call_sid}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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


# ── Call summary ──────────────────────────────────────────────────────────────

class SummarizeRequest(BaseModel):
    goal: str = ""
    transcript: List[dict]   # [{role: "user"|"agent", text: str}, ...]


@router.post("/summarize")
async def summarize_call(body: SummarizeRequest):
    """
    Generate a brief outcome summary for a completed call using the LLM.
    Accepts the goal and transcript collected client-side.
    """
    from openai import AsyncOpenAI

    lines = "\n".join(
        f"{'Caller' if t['role'] == 'user' else 'Agent'}: {t['text']}"
        for t in body.transcript
    )
    goal_line = f"Goal: {body.goal}\n\n" if body.goal else ""
    prompt = (
        f"{goal_line}"
        f"Transcript:\n{lines}\n\n"
        "In 1–2 sentences, summarize the outcome of this call. "
        "Was the goal accomplished? What was agreed or left unresolved?"
    )

    try:
        client = AsyncOpenAI(
            api_key=os.getenv("GROQ_API_KEY", ""),
            base_url="https://api.groq.com/openai/v1",
        )
        resp = await client.chat.completions.create(
            model=os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            temperature=0.3,
        )
        summary = resp.choices[0].message.content.strip()
        return {"summary": summary}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
