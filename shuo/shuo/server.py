"""
FastAPI server for shuo.

Endpoints:
- GET /health - Health check
- GET/POST /twiml - Returns TwiML for Twilio to connect WebSocket
- WebSocket /ws - Media stream endpoint
- GET /trace/latest - Returns the most recent call trace as JSON
- GET /bench/ttft - TTFT benchmark (see ttft.py)
"""

import json
import os
import sys
import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# Ensure project root is on sys.path so dashboard/ and ivr/ are importable
# when running via pipx or any venv that only installed the shuo package.
# PYTHONPATH (set by run.sh) is the reliable path; __file__-based calculation
# only works when running from source, not from a pipx-installed binary.
_project_root = os.environ.get(
    "VOICE_AGENT_ROOT",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, Response, Query
from fastapi.responses import JSONResponse, PlainTextResponse, FileResponse
from twilio.request_validator import RequestValidator
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant

from .conversation import run_conversation
from .services.twilio_isp import TwilioISP
from .services.twilio_client import make_outbound_call, parse_twilio_message
from .services.flux import FluxService
from .services.tts_pool import TTSPool
from .services.flux_pool import FluxPool
from .types import MediaEvent, StreamStopEvent
from .log import get_logger
from .ttft import router as ttft_router
from dashboard.server import router as dashboard_router
from dashboard import bus as dashboard_bus, registry as dashboard_registry

logger = get_logger("shuo.server")


async def verify_twilio_signature(
    request: Request,
    x_twilio_signature: str = Header(None, alias="X-Twilio-Signature"),
) -> None:
    """FastAPI dependency that validates the Twilio request signature.

    Skipped when TWILIO_AUTH_TOKEN is not set (dev-friendly).
    Raises HTTP 403 if signature is missing or invalid.

    For POST routes that carry form data (e.g. /twiml/dial-action/{call_id}),
    Twilio signs the request against the form params, so we extract the form
    body and pass it to the validator.  For GET or form-less POST routes
    Twilio signs against the query-string URL alone (empty params dict).
    """
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
    if not auth_token:
        return  # Skip validation in dev when no auth token configured

    if not x_twilio_signature:
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    # Reconstruct the URL as Twilio sees it using the public base URL.
    public_url = os.getenv("TWILIO_PUBLIC_URL", "")
    if public_url:
        url = public_url.rstrip("/") + str(request.url.path)
        if request.url.query:
            url += "?" + str(request.url.query)
    else:
        url = str(request.url)

    # For POST requests, Twilio signs against the form parameters.
    # We must pass the actual form dict; an empty dict will cause 403 for
    # routes that Twilio POSTs with form data (e.g. dial-action callbacks).
    params: dict = {}
    content_type = request.headers.get("content-type", "")
    if request.method == "POST" and "application/x-www-form-urlencoded" in content_type:
        form_data = await request.form()
        params = dict(form_data)

    validator = RequestValidator(auth_token)
    if not validator.validate(url, params, x_twilio_signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")


app = FastAPI(title="shuo", docs_url=None, redoc_url=None)
app.include_router(dashboard_router)
app.include_router(ttft_router)

# ── Mount IVR mock server at /ivr-mock ───────────────────────────────
try:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
    from ivr.server import app as _ivr_app
    app.mount("/ivr-mock", _ivr_app)
    logger.info("IVR mock mounted at /ivr-mock")
except Exception as _e:
    pass  # IVR mock not available

# ── Graceful shutdown / connection draining ───────────────────────────
_draining = False          # Set True on SIGTERM — reject new calls
_active_calls = 0          # Count of live WebSocket conversations
_drain_event = asyncio.Event()  # Signalled when _active_calls hits 0

# ── DTMF reconnect state (keyed by Twilio call_sid) ──────────────────
_dtmf_pending: dict = {}   # call_sid -> {history, goal, phone, ivr_mode}
_dtmf_lock: asyncio.Lock = asyncio.Lock()  # Protects concurrent access to _dtmf_pending


# ── Global pre-warmed service pools ──────────────────────────────────
_tts_pool: Optional[TTSPool] = None
_flux_pool: Optional[FluxPool] = None


@dataclass
class CallSession:
    """
    Per-call mutable context shared by the closures inside websocket_endpoint.

    Replaces the raw ctx dict to provide type safety and explicit field names.
    call_id may be reassigned on takeover reconnect (preserving the original
    dashboard panel).
    """
    call_id: str
    ivr_mode: bool = False


@app.on_event("startup")
async def startup_warmup() -> None:
    """Pre-load models and warm service connections before the first call."""
    # When the IVR mock is mounted on this server, point IVR_BASE_URL at the
    # main server URL + /ivr-mock so redirect URLs resolve correctly.
    public_url = os.getenv("TWILIO_PUBLIC_URL", "")
    if public_url and not os.getenv("IVR_BASE_URL", "").startswith(public_url):
        os.environ["IVR_BASE_URL"] = f"{public_url}/ivr-mock"
        logger.info(f"IVR base URL set to {public_url}/ivr-mock")
    asyncio.create_task(_warmup())


async def _warmup() -> None:
    global _tts_pool, _flux_pool

    # Pre-load TTS model so the first call doesn't pay model-load latency.
    provider = os.getenv("TTS_PROVIDER", "kokoro").lower()
    if provider == "kokoro":
        try:
            from .services.tts_kokoro import _get_pipeline
            await _get_pipeline()
            logger.info("Kokoro model pre-loaded")
        except Exception as e:
            logger.warning(f"Kokoro pre-load failed: {e}")

    # Global TTS pool — pre-warms a connection so the first call gets one immediately.
    _tts_pool = TTSPool(pool_size=2, ttl=120.0)
    await _tts_pool.start()
    logger.info("Global TTS pool started")

    # Trace file cleanup — remove old/excess traces at startup
    from .tracer import cleanup_traces
    deleted = cleanup_traces()
    if deleted:
        logger.info(f"Startup trace cleanup: removed {deleted} file(s)")

    # NOTE: Flux (Deepgram) connections are NOT pooled.
    # Reusing an idle Deepgram connection causes the turn detector to
    # fire prematurely (calibrated to silence, not live speech).
    # A fresh connection is created per call for correct turn detection.


@app.on_event("shutdown")
async def shutdown_pools() -> None:
    if _tts_pool:
        await _tts_pool.stop()


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/token")
async def get_token():
    """Generate a Twilio Access Token for the browser softphone."""
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    api_key = os.getenv("TWILIO_API_KEY")
    api_secret = os.getenv("TWILIO_API_SECRET")
    token = AccessToken(account_sid, api_key, api_secret, identity="browser")
    token.add_grant(VoiceGrant(incoming_allow=True))
    return {"token": token.to_jwt()}


_SOFTPHONE_DIR = Path(__file__).parent.parent.parent / "softphone"


@app.get("/phone")
async def phone():
    """Browser softphone page for testing — answers calls from the agent."""
    return FileResponse(_SOFTPHONE_DIR / "phone.html")


@app.api_route("/twiml/conference/{call_id}", methods=["GET", "POST"], dependencies=[Depends(verify_twilio_signature)])
async def twiml_conference(call_id: str):
    """Return TwiML to join a takeover conference (used by softphone leg)."""
    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Dial>
        <Conference startConferenceOnEnter="true"
                    endConferenceOnExit="false">takeover-{call_id}</Conference>
    </Dial>
</Response>"""
    return Response(content=twiml_response, media_type="application/xml")


@app.api_route("/twiml/dial-action/{call_id}", methods=["GET", "POST"], dependencies=[Depends(verify_twilio_signature)])
async def dial_action(call_id: str):
    """
    Called by Twilio when the callee's <Dial><Conference> ends.

    Two scenarios:
    1. Callee hung up during takeover → clean up softphone + notify dashboard
    2. Handback redirected the call → this won't fire (redirect skips action)
    """
    call = dashboard_registry.get(call_id)
    if call and call.mode == dashboard_registry.CallMode.TAKEOVER:
        # Callee left conference (hung up) — terminate softphone + clean up
        if call.softphone_call_sid:
            try:
                from twilio.rest import Client
                client = Client(
                    os.getenv("TWILIO_ACCOUNT_SID"),
                    os.getenv("TWILIO_AUTH_TOKEN"),
                )
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, lambda: client.calls(call.softphone_call_sid).update(
                        status="completed"
                    )
                )
            except Exception as e:
                logger.warning(f"Softphone cleanup failed: {e}")
            dashboard_registry.update(call_id, softphone_call_sid="")

        dashboard_bus.publish_global({"call_id": call_id, "type": "call_ended"})
        dashboard_registry.remove(call_id)
        dashboard_bus.destroy(call_id)
        # Callee already hung up — return empty TwiML
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response/>',
            media_type="application/xml",
        )

    # Normal dial-action (non-takeover) — reconnect to agent stream
    public_url = os.getenv("TWILIO_PUBLIC_URL", "")
    ws_url = public_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_url = f"{ws_url}/ws"
    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{ws_url}" track="inbound_track"/>
    </Connect>
</Response>"""
    return Response(content=twiml_response, media_type="application/xml")


@app.api_route("/twiml", methods=["GET", "POST"], dependencies=[Depends(verify_twilio_signature)])
async def twiml(request: Request):
    """
    Return TwiML instructing Twilio to connect a WebSocket stream.

    Twilio calls this URL when the call is answered (or AMD detection completes).
    During graceful shutdown, rejects new calls so they don't get cut off.
    When AMD is enabled, Twilio passes AnsweredBy so we can hang up on voicemail.
    """
    if _draining:
        logger.info("Draining — rejecting new inbound call")
        reject_twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Sorry, we are updating. Please call back in a moment.</Say>
    <Hangup/>
</Response>"""
        return Response(content=reject_twiml, media_type="application/xml")

    # AMD result: Twilio passes AnsweredBy when machine_detection='Enable'
    params = dict(request.query_params)
    if request.method == "POST":
        form = await request.form()
        params.update(dict(form))
    answered_by = params.get("AnsweredBy", "")
    logger.info(f"AMD AnsweredBy={answered_by!r} (all params: {list(params.keys())})")
    # Hang up only on confirmed machine/voicemail. "unknown" means AMD couldn't
    # determine — treat as human to avoid hanging up on real people.
    _machine_values = {"machine_start", "machine_end_beep", "machine_end_silence", "machine_end_other", "fax"}
    if answered_by and answered_by in _machine_values:
        logger.info(f"AMD: {answered_by} — hanging up without greeting")
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
            media_type="application/xml",
        )

    public_url = os.getenv("TWILIO_PUBLIC_URL", "")
    ws_url = public_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_url = f"{ws_url}/ws"

    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect record="record-from-answer-dual">
        <Stream url="{ws_url}" track="inbound_track">
            <Parameter name="from" value="{{From}}"/>
        </Stream>
    </Connect>
</Response>"""

    return Response(content=twiml_response, media_type="application/xml")


@app.api_route("/twiml/ivr-dtmf", methods=["GET", "POST"], dependencies=[Depends(verify_twilio_signature)])
async def twiml_ivr_dtmf(digit: str = Query(..., description="DTMF digit(s) to play")):
    """
    Return TwiML that plays DTMF digit(s) to the remote party then reconnects
    the WebSocket stream. Used by the agent to navigate IVR menus via REST API.
    """
    public_url = os.getenv("TWILIO_PUBLIC_URL", "")
    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play digits="{digit}"/>
    <Redirect>{public_url}/twiml</Redirect>
</Response>"""
    return Response(content=twiml_response, media_type="application/xml")


@app.get("/trace/latest")
async def latest_trace():
    """Return the most recent call trace as JSON."""
    trace_dir = Path("/tmp/shuo")
    if not trace_dir.exists():
        return JSONResponse({"error": "No traces found"}, status_code=404)

    traces = sorted(trace_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not traces:
        return JSONResponse({"error": "No traces found"}, status_code=404)

    data = json.loads(traces[0].read_text())
    return JSONResponse(data)


@app.get("/call/{phone_number:path}")
async def trigger_call(phone_number: str, goal: Optional[str] = Query(None)):
    """
    Initiate an outbound call.

    Usage:
        curl https://your-server/call/+1234567890
        curl https://your-server/call/+1234567890?goal=ask+about+pricing
    """
    if not phone_number.startswith("+"):
        phone_number = f"+{phone_number}"
    try:
        call_sid = make_outbound_call(phone_number)
        effective_goal = goal or os.getenv("CALL_GOAL", "")
        dashboard_registry.set_pending(call_sid, phone_number, effective_goal)
        return {"status": "calling", "to": phone_number, "call_sid": call_sid, "goal": effective_goal}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)



@app.websocket("/ws-listen")
async def ws_listen(websocket: WebSocket):
    """
    Listen-only WebSocket for take-over mode.

    Twilio strips query parameters from WebSocket URLs, so the call is
    identified from the Twilio 'start' event: first via customParameters
    (call_id injected via <Parameter> in the TwiML), then via callSid lookup.

    Feeds callee (inbound) and human (outbound) audio to separate Deepgram
    Flux instances for independent per-speaker transcription.
    """
    import base64 as _b64

    await websocket.accept()

    # ── Identify the call from the Twilio start event ─────────────────
    call_id = ""
    call = None
    try:
        while True:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
            data = json.loads(raw)
            event_type = data.get("event")
            if event_type == "connected":
                continue
            if event_type == "start":
                start_data = data.get("start", {})
                custom = start_data.get("customParameters", {})
                call_id = custom.get("call_id", "")
                if call_id:
                    call = dashboard_registry.get(call_id)
                if not call:
                    # Fallback: look up by Twilio call SID
                    call_sid = start_data.get("callSid", "")
                    if call_sid:
                        call = dashboard_registry.find_by_call_sid(call_sid)
                        if call:
                            call_id = call.call_id
                break
            if event_type == "stop":
                await websocket.close()
                return
    except asyncio.TimeoutError:
        logger.warning("ws-listen: timed out waiting for start event")
        await websocket.close()
        return
    except Exception as e:
        logger.warning(f"ws-listen: error during handshake: {e}")
        await websocket.close()
        return

    if not call:
        logger.warning(f"ws-listen: could not identify call (call_id={call_id!r})")
        await websocket.close()
        return

    logger.info(f"ws-listen connected for call {call_id}")

    # ── Set up per-speaker Flux instances ────────────────────────────
    def make_on_end_of_turn(speaker: str):
        async def _cb(transcript: str) -> None:
            if not transcript:
                return
            c = dashboard_registry.get(call_id)
            if c:
                c.takeover_transcript.append(transcript)
            dashboard_bus.publish_global({
                "call_id": call_id,
                "type":    "transcript",
                "speaker": speaker,
                "text":    transcript,
            })
        return _cb

    async def _noop_start() -> None:
        pass

    flux_callee = FluxService(
        on_end_of_turn=make_on_end_of_turn("callee"),
        on_start_of_turn=_noop_start,
    )
    flux_human = FluxService(
        on_end_of_turn=make_on_end_of_turn("human"),
        on_start_of_turn=_noop_start,
    )
    await flux_callee.start()
    await flux_human.start()

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            event_type = data.get("event")
            if event_type == "media":
                media = data.get("media", {})
                payload = media.get("payload", "")
                if payload:
                    audio = _b64.b64decode(payload)
                    # "inbound" = callee speaking; "outbound" = what callee hears (= human)
                    if media.get("track") == "outbound":
                        await flux_human.send(audio)
                    else:
                        await flux_callee.send(audio)
            elif event_type == "stop":
                break
    except Exception as e:
        logger.warning(f"ws-listen error for {call_id}: {e}")
    finally:
        await flux_callee.stop()
        await flux_human.stop()
        logger.info(f"ws-listen disconnected for call {call_id}")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for Twilio Media Streams.

    Handles the bidirectional audio stream for a single call.
    Wires the dashboard event bus and registry for real-time monitoring.
    Supports reconnection after take-over hand-back.
    """
    global _active_calls

    await websocket.accept()
    _active_calls += 1

    # Typed per-call context — call_id may be reassigned on takeover reconnect
    ctx = CallSession(call_id=uuid.uuid4().hex[:8])
    dashboard_bus.create(ctx.call_id)
    dashboard_registry.register(dashboard_registry.ActiveCall(call_id=ctx.call_id))

    def observer(event: dict) -> None:
        tagged = {**event, "call_id": ctx.call_id}
        # On stream_start: embed phone and goal into the broadcast so the
        # dashboard panel is created with both values in a single event.
        if event.get("type") == "stream_start":
            c = dashboard_registry.get(ctx.call_id)
            tagged = {**tagged, "phone": c.phone if c else "", "goal": c.goal if c else ""}
        dashboard_bus.publish_global(tagged)

    def should_suppress_agent() -> bool:
        c = dashboard_registry.get(ctx.call_id)
        return c is not None and c.mode == dashboard_registry.CallMode.TAKEOVER

    def on_agent_ready(agent) -> None:
        dashboard_registry.update(ctx.call_id, agent=agent)

    logger.info(f"Call connected  (active: {_active_calls})")

    def get_goal(call_sid: str) -> str:
        # Pop the pending phone+goal and store both in the registry so the
        # observer can embed them in the stream_start broadcast.
        pending = dashboard_registry.pop_pending(call_sid)
        goal = pending["goal"] or os.getenv("CALL_GOAL", "")
        phone = pending["phone"]
        ctx.ivr_mode = pending.get("ivr_mode", False)
        dashboard_registry.update(ctx.call_id, goal=goal, phone=phone, call_sid=call_sid)
        return goal

    async def on_dtmf(digits: str) -> None:
        """Save agent history for reconnection after DTMF redirect.

        The actual REST redirect is handled by TwilioISP.send_dtmf().
        """
        c = dashboard_registry.get(ctx.call_id)
        if not c or not c.call_sid:
            logger.warning("on_dtmf: no call_sid found")
            return
        call_sid = c.call_sid
        agent = c.agent
        history = agent.history if agent else []
        async with _dtmf_lock:
            _dtmf_pending[call_sid] = {
                "history": history,
                "goal": c.goal,
                "phone": c.phone,
                "ivr_mode": True,
            }

    async def get_saved_state(call_sid: str):
        """Check if this stream reconnects after a DTMF redirect or take-over."""
        # DTMF reconnect: agent pressed a key, call was redirected
        async with _dtmf_lock:
            saved_dtmf = _dtmf_pending.pop(call_sid, None)
        if saved_dtmf:
            ctx.ivr_mode = True
            goal = saved_dtmf["goal"]
            phone = saved_dtmf["phone"]
            dashboard_registry.update(ctx.call_id, goal=goal, phone=phone, call_sid=call_sid)
            logger.info(f"DTMF reconnect for call_sid={call_sid} goal={goal!r}")
            return {
                "history": saved_dtmf["history"],
                "takeover_transcript": [],  # Empty → no handback prompt, agent listens
                "goal": goal,
                "phone": phone,
            }

        # Takeover reconnect: check if this stream reconnects a call that was in take-over.
        existing = dashboard_registry.find_by_call_sid(call_sid)
        if existing and existing.call_id != ctx.call_id and existing.saved_history:
            result = {
                "history": existing.saved_history,
                "takeover_transcript": existing.takeover_transcript,
                "goal": existing.goal,
                "phone": existing.phone,
            }
            # Clean up the temporary entry and bus
            dashboard_registry.remove(ctx.call_id)
            dashboard_bus.destroy(ctx.call_id)
            # Reuse the original call_id so dashboard panel stays intact
            ctx.call_id = existing.call_id
            dashboard_registry.update(ctx.call_id,
                mode=dashboard_registry.CallMode.AGENT,
                saved_history=[],
                takeover_transcript=[],
                listen_stream_sid="",
                call_sid=call_sid,
            )
            dashboard_bus.publish_global({
                "call_id": ctx.call_id,
                "type": "stream_start",
                "phone": result["phone"],
                "goal": result["goal"],
            })
            logger.info(f"Reconnected call {ctx.call_id} after take-over")
            return result
        return None

    isp = TwilioISP(websocket)
    try:
        await run_conversation(
            isp,
            observer=observer,
            should_suppress_agent=should_suppress_agent,
            on_agent_ready=on_agent_ready,
            get_goal=get_goal,
            on_dtmf=on_dtmf,
            get_saved_state=get_saved_state,
            tts_pool=_tts_pool,
            ivr_mode=lambda: ctx.ivr_mode,
        )
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        _active_calls -= 1
        cid = ctx.call_id
        call = dashboard_registry.get(cid)
        if call and call.mode == dashboard_registry.CallMode.TAKEOVER:
            # Preserve registry entry for reconnection after hand-back
            if call.agent:
                dashboard_registry.update(cid, saved_history=call.agent.history)
            logger.info(f"Call {cid} paused for takeover  (active: {_active_calls})")
        else:
            dashboard_registry.remove(cid)
            dashboard_bus.destroy(cid)
            logger.info(f"Call ended  (active: {_active_calls})")
        if _draining and _active_calls <= 0:
            _drain_event.set()