"""
llm_api.py — HTTP API for stateful LLM sessions.

Exposes LanguageModel (shuo/language.py) over HTTP so external tools
(e.g. dialact-eval) can drive LLM turns without importing shuo directly.

LanguageModel is the single implementation used by both the production
phone call path (via agent.py) and these HTTP endpoints.

Routes (mounted at /llm in web.py):
  POST   /sessions               — create session, returns {session_id}
  POST   /sessions/{id}/generate — blocking generate, returns TurnResult JSON
  POST   /sessions/{id}/stream   — SSE token stream
  DELETE /sessions/{id}          — delete session
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import AsyncIterator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .context import CallContext
from .language import LanguageModel, TurnResult
from .log import get_logger

logger = get_logger("shuo.llm_api")

_SESSION_TTL_MINUTES = int(os.getenv("LLM_SESSION_TTL_MINUTES", "30"))


# =============================================================================
# SESSION STORE
# =============================================================================

@dataclass
class _SessionEntry:
    model:     LanguageModel
    lock:      asyncio.Lock
    last_used: datetime


_sessions: dict[str, _SessionEntry] = {}
_cleanup_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]


async def _cleanup_loop() -> None:
    """Background task: remove sessions idle longer than _SESSION_TTL_MINUTES."""
    while True:
        await asyncio.sleep(60)
        cutoff = datetime.utcnow() - timedelta(minutes=_SESSION_TTL_MINUTES)
        expired = [sid for sid, e in _sessions.items() if e.last_used < cutoff]
        for sid in expired:
            _sessions.pop(sid, None)
            logger.info(f"Expired idle LLM session {sid}")


def start_cleanup_task() -> None:
    global _cleanup_task
    _cleanup_task = asyncio.create_task(_cleanup_loop())


def stop_cleanup_task() -> None:
    if _cleanup_task:
        _cleanup_task.cancel()


def _get_session(session_id: str) -> _SessionEntry:
    entry = _sessions.get(session_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")
    entry.last_used = datetime.utcnow()
    return entry


# =============================================================================
# REQUEST MODELS
# =============================================================================

class _GenerateRequest(BaseModel):
    message: str


# =============================================================================
# ROUTER
# =============================================================================

router = APIRouter(tags=["llm"])


@router.post("/sessions", status_code=201)
async def create_session(body: dict) -> dict:
    """
    Create a stateful LLM session.

    Body: a CallContext JSON object (same shape as POST /call/{phone} body),
    plus an optional `callee_lang` field.
    Returns: {"session_id": "<uuid4>"}
    """
    callee_lang = body.pop("callee_lang", "English")
    goal = body.get("goal", "")

    try:
        ctx = CallContext(**body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    model = LanguageModel(ctx=ctx, callee_lang=callee_lang)
    session_id = str(uuid.uuid4())
    _sessions[session_id] = _SessionEntry(
        model=model,
        lock=asyncio.Lock(),
        last_used=datetime.utcnow(),
    )
    logger.info(f"Created LLM session {session_id} (goal={goal!r})")
    return {"session_id": session_id}


@router.post("/sessions/{session_id}/generate")
async def generate(session_id: str, req: _GenerateRequest) -> dict:
    """
    Run a single LLM turn (blocking).

    Acquires a per-session lock so concurrent calls are serialised.
    Returns TurnResult as JSON.
    """
    entry = _get_session(session_id)
    async with entry.lock:
        entry.last_used = datetime.utcnow()
        result = await entry.model.generate(req.message)
    return dataclasses.asdict(result)


@router.post("/sessions/{session_id}/stream")
async def stream(session_id: str, req: _GenerateRequest) -> StreamingResponse:
    """
    Run an LLM turn and stream tokens as Server-Sent Events.

    Events:
      data: {"type": "token", "text": "<token>"}  — one per speech token
      data: {"type": "done", ...TurnResult fields} — final event
    """
    entry = _get_session(session_id)

    async def _sse() -> AsyncIterator[str]:
        async with entry.lock:
            entry.last_used = datetime.utcnow()
            async for item in entry.model.iter_stream(req.message):
                if isinstance(item, str):
                    yield f"data: {json.dumps({'type': 'token', 'text': item})}\n\n"
                else:
                    payload = {"type": "done", **dataclasses.asdict(item)}
                    yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(_sse(), media_type="text/event-stream")


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str) -> None:
    """Delete an LLM session and free its resources."""
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")
    _sessions.pop(session_id)
    logger.info(f"Deleted LLM session {session_id}")
