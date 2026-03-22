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
import asyncio
import collections
from pathlib import Path
from typing import List

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import bus as dashboard_bus
from . import registry

import logging
_log = logging.getLogger("dashboard.server")

_HERE = Path(__file__).parent

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ── Auth dependency ───────────────────────────────────────────────────────────

def verify_api_key(x_api_key: str = Header(None, alias="X-API-Key")) -> None:
    """Verify X-API-Key header against DASHBOARD_API_KEY env var.

    Auth is disabled (passes through) when DASHBOARD_API_KEY is unset or empty.
    """
    expected = os.getenv("DASHBOARD_API_KEY", "")
    if not expected:
        return  # Auth disabled when env var unset
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")


# ── Rate limiter ──────────────────────────────────────────────────────────────

class _RateLimiter:
    """In-process per-IP rate limiter using a sliding window."""

    def __init__(self):
        self._hits: dict[str, list[float]] = collections.defaultdict(list)

    def check(self, ip: str, limit: int, window: float) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds). Prunes expired entries."""
        now = time.monotonic()
        cutoff = now - window
        hits = self._hits[ip]
        # Prune old entries
        self._hits[ip] = hits = [t for t in hits if t > cutoff]
        if len(hits) >= limit:
            retry_after = int(hits[0] - cutoff) + 1
            return False, retry_after
        hits.append(now)
        return True, 0


_call_limiter = _RateLimiter()


# ── Pages ────────────────────────────────────────────────────────────────────

@router.get("/", dependencies=[Depends(verify_api_key)])
async def dashboard_page():
    """Serve the supervisor dashboard UI."""
    return FileResponse(_HERE / "app.html")


# ── WebSocket event stream ────────────────────────────────────────────────────

@router.websocket("/ws")
async def dashboard_ws(websocket: WebSocket, token: str = Query(None)):
    """
    Push all call events to a connected supervisor browser.

    On connect: sends current active call state.
    Ongoing: forwards every event from every call (tagged with call_id).
    """
    expected = os.getenv("DASHBOARD_API_KEY", "")
    if expected and token != expected:
        await websocket.close(code=4003, reason="Missing or invalid token")
        return
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

@router.get("/calls", dependencies=[Depends(verify_api_key)])
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

@router.post("/calls/{call_id}/hangup", dependencies=[Depends(verify_api_key)])
async def hangup(call_id: str):
    """Terminate the call via Twilio REST API."""
    call = registry.get(call_id)
    if not call:
        return JSONResponse({"error": "Call not found"}, status_code=404)

    if not call.call_sid:
        return JSONResponse({"error": "Call SID not yet available"}, status_code=503)

    loop = asyncio.get_event_loop()
    try:
        from twilio.rest import Client
        client = Client(
            os.getenv("TWILIO_ACCOUNT_SID"),
            os.getenv("TWILIO_AUTH_TOKEN"),
        )

        # Hang up softphone leg first if in takeover
        if call.softphone_call_sid:
            try:
                await loop.run_in_executor(
                    None, lambda: client.calls(call.softphone_call_sid).update(
                        status="completed"
                    )
                )
            except Exception:
                pass
            registry.update(call_id, softphone_call_sid="")

        # Hang up the callee's call
        try:
            await loop.run_in_executor(
                None, lambda: client.calls(call.call_sid).update(status="completed")
            )
        except Exception as e:
            _log.warning(f"Callee hangup failed (may already be ended): {e}")

        # Clean up registry and bus
        registry.remove(call_id)
        dashboard_bus.destroy(call_id)
        dashboard_bus.publish_global({"call_id": call_id, "type": "call_ended"})
        return {"status": "ok"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Control: takeover / handback ─────────────────────────────────────────────

@router.post("/calls/{call_id}/takeover", dependencies=[Depends(verify_api_key)])
async def takeover(call_id: str):
    """
    Join the live call as a three-way conference.

    1. Save agent conversation history
    2. Redirect the callee into a conference room
    3. Dial the browser softphone into the same conference
    4. Create a listen-only stream so the agent tracks the conversation
    5. Agent is suppressed but keeps listening
    """
    call = registry.get(call_id)
    if not call:
        return JSONResponse({"error": "Call not found"}, status_code=404)
    if not call.call_sid:
        return JSONResponse({"error": "Call SID not yet available"}, status_code=503)

    # Save agent history before redirecting (stream will close)
    if call.agent:
        registry.update(call_id, saved_history=call.agent.history)

    registry.update(call_id, mode=registry.CallMode.TAKEOVER, takeover_transcript=[])
    dashboard_bus.publish_global({"call_id": call_id, "type": "takeover"})

    loop = asyncio.get_event_loop()
    try:
        from twilio.rest import Client
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        public_url = os.getenv("TWILIO_PUBLIC_URL", "")
        from_number = os.getenv("TWILIO_PHONE_NUMBER", "")
        client = Client(account_sid, auth_token)
        conf_name = f"takeover-{call_id}"

        # 1. Redirect callee into the conference with a <Start><Stream>
        #    to fork audio for real-time transcription during takeover
        ws_url = public_url.replace("https://", "wss://").replace("http://", "ws://")
        listen_url = f"{ws_url}/ws-listen"
        conf_twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            f'<Start><Stream url="{listen_url}" track="both_tracks">'
            f'<Parameter name="call_id" value="{call_id}"/>'
            f'</Stream></Start>'
            f'<Dial action="{public_url}/twiml/dial-action/{call_id}">'
            f'<Conference startConferenceOnEnter="true" '
            f'endConferenceOnExit="false">{conf_name}</Conference>'
            "</Dial>"
            "</Response>"
        )
        await loop.run_in_executor(
            None, lambda: client.calls(call.call_sid).update(twiml=conf_twiml)
        )

        # 2. Dial the browser softphone into the same conference
        softphone_call = await loop.run_in_executor(
            None, lambda: client.calls.create(
                to="client:browser",
                from_=from_number,
                url=f"{public_url}/twiml/conference/{call_id}",
            )
        )
        registry.update(call_id, softphone_call_sid=softphone_call.sid)

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    return {"status": "ok"}


@router.post("/calls/{call_id}/handback", dependencies=[Depends(verify_api_key)])
async def handback(call_id: str):
    """
    Remove the human from the conference and return control to the agent.

    1. Hang up the softphone call leg (leaves conference)
    2. Redirect the callee back to <Connect><Stream>
    3. The agent conversation loop reconnects with full history
    """
    call = registry.get(call_id)
    if not call:
        return JSONResponse({"error": "Call not found"}, status_code=404)
    if not call.call_sid:
        return JSONResponse({"error": "Call SID not yet available"}, status_code=503)

    registry.update(call_id, mode=registry.CallMode.AGENT)
    dashboard_bus.publish_global({"call_id": call_id, "type": "handback"})

    loop = asyncio.get_event_loop()
    try:
        from twilio.rest import Client
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        public_url = os.getenv("TWILIO_PUBLIC_URL", "")
        client = Client(account_sid, auth_token)

        # Hang up the softphone leg
        if call.softphone_call_sid:
            try:
                await loop.run_in_executor(
                    None, lambda: client.calls(call.softphone_call_sid).update(
                        status="completed"
                    )
                )
            except Exception as e:
                _log.warning(f"Softphone hangup failed: {e}")
            registry.update(call_id, softphone_call_sid="")

        # Redirect callee back to the normal Connect+Stream TwiML
        twiml_url = f"{public_url}/twiml"
        await loop.run_in_executor(
            None, lambda: client.calls(call.call_sid).update(url=twiml_url)
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    return {"status": "ok"}


# ── Control: DTMF injection ───────────────────────────────────────────────────

class CallRequest(BaseModel):
    phone: str
    goal: str = ""
    ivr_mode: bool = False  # When True: suppress opener, force DTMF-only navigation


@router.post("/call", dependencies=[Depends(verify_api_key)])
async def start_call(body: CallRequest, request: Request):
    """
    Trigger an outbound call from the dashboard UI.

    Stores the goal keyed by call SID so the agent picks it up when the
    WebSocket stream_start event arrives.

    Set ivr_mode=True when calling an automated IVR system — suppresses the
    opening greeting and forces DTMF-only navigation mode.
    """
    limit = int(os.getenv("CALL_RATE_LIMIT", "10"))
    ip = request.client.host if request.client else "unknown"
    allowed, retry_after = _call_limiter.check(ip, limit, window=60.0)
    if not allowed:
        return JSONResponse(
            {"error": "Rate limit exceeded"},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    phone = body.phone.strip()
    if not phone.startswith("+") and not phone.startswith("client:"):
        phone = f"+{phone}"

    try:
        from shuo.services.twilio_client import make_outbound_call
        call_sid = make_outbound_call(phone, ivr_mode=body.ivr_mode)
        registry.set_pending(call_sid, phone=phone, goal=body.goal, ivr_mode=body.ivr_mode)
        return {"status": "calling", "to": phone, "call_sid": call_sid}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


class DTMFRequest(BaseModel):
    digit: str


@router.post("/calls/{call_id}/dtmf", dependencies=[Depends(verify_api_key)])
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


@router.post("/summarize", dependencies=[Depends(verify_api_key)])
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
