"""
report.py — Call task report generation.

Produces a structured report of a completed call combining:
  - Semantic task:   goal, agent persona, success criteria, constraints
  - Conversation:    turn-by-turn dialogue transcript
  - IVR navigation:  DTMF presses and menu traversal sequence
  - Call transport:  metadata, timing, hold events
  - Outcome:         LLM-evaluated goal completion assessment

Reports are saved under DATA_DIR/calls/<tenant_id>/<call_id>_report.json.
"""

import asyncio
import json
import uuid
import datetime
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Any

from .log import get_logger
from .store import get_call_data_dir, get_data_dir

logger = get_logger("shuo.report")


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class ConversationTurn:
    """A single exchange in the call conversation."""
    turn: int
    speaker_text: str           # What the callee/service said (empty for opening greeting)
    agent_text: str             # What our agent responded (empty for DTMF-only turns)
    dtmf_pressed: Optional[str] = None  # Digit(s) pressed for IVR navigation
    cancelled: bool = False             # True if barge-in interrupted this turn


@dataclass
class IVRNavigation:
    """IVR menu navigation via DTMF keypad presses."""
    dtmf_sequence: List[str] = field(default_factory=list)  # Ordered list, e.g. ["1", "3", "#"]
    total_presses: int = 0


@dataclass
class CallTransport:
    """Physical telephony layer details for the call."""
    call_id: str
    phone_number: str
    started_at: str             # ISO 8601 UTC
    ended_at: Optional[str]     # ISO 8601 UTC
    duration_s: Optional[float]
    total_turns: int            # Agent turns including opening greeting
    barge_in_count: int         # Turns cancelled by caller interrupting
    hold_count: int             # Times agent detected it was placed on hold
    ivr_navigation: IVRNavigation


@dataclass
class TaskReport:
    """
    Complete post-call report: semantic task definition + underlying transport.

    call_disposition captures the telephony outcome regardless of whether a
    conversation took place:
      "connected"  — call answered, agent ran
      "busy"       — remote party returned busy signal
      "no-answer"  — call rang but was not answered
      "failed"     — call could not be placed (routing/carrier error)
      "cancelled"  — call was cancelled before it was answered
    """
    report_id: str
    generated_at: str           # ISO 8601 UTC

    # Identifiers
    call_id: str
    tenant_id: str

    # ── Semantic task ──────────────────────────────────────────────────────────
    goal: str
    agent_name: Optional[str]
    agent_role: str
    agent_tone: str
    success_criteria: Optional[str]
    constraints: List[str]
    caller_name: Optional[str]
    caller_context: Optional[str]

    # ── Telephony disposition ──────────────────────────────────────────────────
    call_disposition: str       # connected | busy | no-answer | failed | cancelled

    # ── Conversation ───────────────────────────────────────────────────────────
    conversation: List[ConversationTurn]

    # ── Transport ──────────────────────────────────────────────────────────────
    transport: CallTransport

    # ── Performance (from telemetry call_summary) ──────────────────────────────
    performance: dict

    # ── Outcome (LLM-assessed, None if assessment fails or is skipped) ─────────
    goal_achieved: Optional[bool] = None
    outcome_summary: Optional[str] = None


# =============================================================================
# REPORT BUILDER
# =============================================================================

class ReportBuilder:
    """
    Collects call events during a call and generates a TaskReport at the end.

    Usage (inside run_call):
        builder = ReportBuilder()
        builder.set_task(goal, ctx)
        builder.set_phone(phone_number)
        # ... feed events during call ...
        report = await builder.finalize(call_id, call_summary, tenant_id)
        save_report(report, tenant_id)
    """

    def __init__(self) -> None:
        self._started_at: str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        self._ended_at: Optional[str] = None
        self._phone_number: str = ""

        # Task fields — populated by set_task()
        self._goal: str = ""
        self._agent_name: Optional[str] = None
        self._agent_role: str = "a professional assistant"
        self._agent_tone: str = "friendly and concise"
        self._success_criteria: Optional[str] = None
        self._constraints: List[str] = []
        self._caller_name: Optional[str] = None
        self._caller_context: Optional[str] = None

        # Conversation accumulation
        self._turns: List[ConversationTurn] = []
        self._turn_counter: int = 0
        self._pending_speaker_text: str = ""  # What callee said this turn

        # Transport counters
        self._dtmf_sequence: List[str] = []
        self._hold_count: int = 0
        self._barge_in_count: int = 0

    # ── Configuration ────────────────────────────────────────────────────────

    def set_task(self, goal: str, ctx: Any = None, caller_name: Optional[str] = None) -> None:
        """Set the call goal and optional full CallContext."""
        self._goal = goal
        if ctx is not None:
            self._agent_name = getattr(ctx, "agent_name", None)
            self._agent_role = getattr(ctx, "agent_role", "a professional assistant")
            self._agent_tone = getattr(ctx, "agent_tone", "friendly and concise")
            self._success_criteria = getattr(ctx, "success_criteria", None)
            self._constraints = list(getattr(ctx, "constraints", []))
            self._caller_name = getattr(ctx, "caller_name", None)
            self._caller_context = getattr(ctx, "caller_context", None)
        if caller_name is not None:
            self._caller_name = caller_name

    def set_phone(self, phone_number: str) -> None:
        self._phone_number = phone_number

    # ── Event hooks ──────────────────────────────────────────────────────────

    def on_user_spoke(self, transcript: str) -> None:
        """Called when the callee/service finishes a speech turn."""
        self._pending_speaker_text = transcript

    def on_agent_done(self, agent_text: str, cancelled: bool = False) -> None:
        """Called when the agent finishes a spoken response (no DTMF this turn)."""
        self._turn_counter += 1
        self._turns.append(ConversationTurn(
            turn=self._turn_counter,
            speaker_text=self._pending_speaker_text,
            agent_text=agent_text,
            cancelled=cancelled,
        ))
        if cancelled:
            self._barge_in_count += 1
        self._pending_speaker_text = ""

    def on_dtmf(self, digits: str, agent_text: str = "") -> None:
        """Called when the agent sends DTMF (IVR menu navigation)."""
        self._dtmf_sequence.append(digits)
        self._turn_counter += 1
        self._turns.append(ConversationTurn(
            turn=self._turn_counter,
            speaker_text=self._pending_speaker_text,
            agent_text=agent_text,
            dtmf_pressed=digits,
        ))
        self._pending_speaker_text = ""

    def on_hold_start(self) -> None:
        """Called when the agent detects it has been placed on hold."""
        self._hold_count += 1

    def on_call_ended(self) -> None:
        """Called when the call disconnects (either party)."""
        if not self._ended_at:
            self._ended_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    # ── Finalize ─────────────────────────────────────────────────────────────

    async def finalize(
        self,
        call_id: str,
        call_summary: dict,
        tenant_id: str = "default",
        assess: bool = True,
        partial_agent_text: str = "",
    ) -> "TaskReport":
        """
        Build the final TaskReport.

        Parameters
        ----------
        call_id:             Call identifier (stream_sid from Twilio).
        call_summary:        Telemetry summary dict from CallTelemetry.summary().
        tenant_id:           Resolved tenant identifier.
        assess:              Run LLM goal-achievement assessment (requires GROQ_API_KEY).
        partial_agent_text:  In-progress agent text if call ended mid-turn.
        """
        if not self._ended_at:
            self.on_call_ended()

        # Flush any pending turn (caller spoke but call ended before agent finished responding)
        if self._pending_speaker_text or partial_agent_text:
            self._turn_counter += 1
            self._turns.append(ConversationTurn(
                turn=self._turn_counter,
                speaker_text=self._pending_speaker_text,
                agent_text=partial_agent_text,
            ))
            self._pending_speaker_text = ""

        # Derive duration from telemetry first, fall back to wall-clock
        duration_s: Optional[float] = None
        total_call_ms = (call_summary or {}).get("durations", {}).get("total_call_ms")
        if total_call_ms is not None:
            duration_s = round(total_call_ms / 1000, 2)
        else:
            try:
                started = datetime.datetime.fromisoformat(self._started_at.rstrip("Z"))
                ended = datetime.datetime.fromisoformat(self._ended_at.rstrip("Z"))  # type: ignore[arg-type]
                duration_s = round((ended - started).total_seconds(), 2)
            except Exception:
                pass

        report = TaskReport(
            report_id=uuid.uuid4().hex[:16],
            generated_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            call_id=call_id,
            tenant_id=tenant_id,
            goal=self._goal,
            agent_name=self._agent_name,
            agent_role=self._agent_role,
            agent_tone=self._agent_tone,
            success_criteria=self._success_criteria,
            constraints=self._constraints,
            caller_name=self._caller_name,
            caller_context=self._caller_context,
            call_disposition="connected",
            conversation=list(self._turns),
            transport=CallTransport(
                call_id=call_id,
                phone_number=self._phone_number,
                started_at=self._started_at,
                ended_at=self._ended_at,
                duration_s=duration_s,
                total_turns=self._turn_counter,
                barge_in_count=self._barge_in_count,
                hold_count=self._hold_count,
                ivr_navigation=IVRNavigation(
                    dtmf_sequence=list(self._dtmf_sequence),
                    total_presses=len(self._dtmf_sequence),
                ),
            ),
            performance=call_summary or {},
        )

        if assess and self._goal:
            try:
                achieved, summary = await _assess_goal(
                    goal=self._goal,
                    success_criteria=self._success_criteria,
                    conversation=self._turns,
                )
                report.goal_achieved = achieved
                report.outcome_summary = summary
            except Exception as exc:
                logger.warning(f"Goal assessment failed: {exc}")

        return report


# =============================================================================
# PERSISTENCE
# =============================================================================

def save_report(report: TaskReport, tenant_id: str = "default") -> Path:
    """Write report JSON to DATA_DIR/calls/<tenant_id>/<call_id>_report.json.

    Also persists to PostgreSQL asynchronously when a database pool is available.
    """
    report_dir = get_call_data_dir(tenant_id)
    path = report_dir / f"{report.call_id}_report.json"
    path.write_text(json.dumps(asdict(report), indent=2))
    logger.info(f"Report saved → {path}")

    # Fire-and-forget DB write with a bounded timeout so a slow/hung DB
    # connection never leaks into the event loop indefinitely.
    from . import db as _db
    if _db.is_available():
        try:
            async def _save_with_timeout(report_dict: dict) -> None:
                try:
                    await asyncio.wait_for(_db.save_call_log(report_dict), timeout=12.0)
                except Exception as _exc:
                    logger.warning(f"DB report write failed: {_exc}")

            if asyncio.get_event_loop().is_running():
                asyncio.ensure_future(_save_with_timeout(asdict(report)))
        except Exception as _exc:
            logger.warning(f"DB report schedule failed: {_exc}")

    return path


def load_latest_report() -> Optional[dict]:
    """Return the most recently written report dict across all tenant dirs."""
    scan_root = get_data_dir() / "calls"
    if not scan_root.exists():
        return None
    reports = sorted(
        scan_root.glob("**/*_report.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return json.loads(reports[0].read_text()) if reports else None


async def load_report(call_id: str, tenant_id: str = "default") -> Optional[dict]:
    """Return a specific report by call_id and tenant_id, or None if not found.

    Reads from PostgreSQL when available, falling back to local JSON.
    """
    from . import db as _db
    if _db.is_available():
        try:
            result = await asyncio.wait_for(_db.get_call_log(call_id, tenant_id), timeout=5.0)
            if result is not None:
                return result
        except Exception as _exc:
            logger.warning(f"DB report load failed, falling back to JSON: {_exc}")

    path = get_call_data_dir(tenant_id) / f"{call_id}_report.json"
    return json.loads(path.read_text()) if path.exists() else None


async def list_reports(
    tenant_id: Optional[str] = None,
    limit: int = 100,
) -> list:
    """Return report metadata for all saved calls, newest first.

    Args:
        tenant_id: Filter to a specific tenant.  None = all tenants.
        limit: Maximum number of entries to return.

    Each entry is a lightweight dict containing the fields most useful for a
    call-history listing (not the full conversation transcript):
        call_id, tenant_id, phone_number, started_at, ended_at, duration_s,
        goal, call_disposition, goal_achieved, outcome_summary, total_turns,
        barge_in_count, report_id, generated_at
    """
    from . import db as _db
    if _db.is_available():
        try:
            return await asyncio.wait_for(
                _db.list_call_logs(tenant_id=tenant_id, limit=limit),
                timeout=5.0,
            )
        except Exception as _exc:
            logger.warning(f"DB list_reports failed, falling back to JSON: {_exc}")

    scan_root = get_data_dir() / "calls"
    if not scan_root.exists():
        return []

    if tenant_id:
        pattern = f"{tenant_id}/*_report.json"
    else:
        pattern = "**/*_report.json"

    paths = sorted(
        scan_root.glob(pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]

    results = []
    for p in paths:
        try:
            data = json.loads(p.read_text())
            transport = data.get("transport", {})
            results.append({
                "call_id":        data.get("call_id", ""),
                "tenant_id":      data.get("tenant_id", ""),
                "phone_number":   transport.get("phone_number", ""),
                "started_at":     transport.get("started_at", ""),
                "ended_at":       transport.get("ended_at"),
                "duration_s":     transport.get("duration_s"),
                "goal":           data.get("goal", ""),
                "call_disposition": data.get("call_disposition", ""),
                "goal_achieved":  data.get("goal_achieved"),
                "outcome_summary": data.get("outcome_summary"),
                "total_turns":    transport.get("total_turns", 0),
                "barge_in_count": transport.get("barge_in_count", 0),
                "report_id":      data.get("report_id", ""),
                "generated_at":   data.get("generated_at", ""),
            })
        except Exception:
            pass

    return results


def build_disposition_report(
    call_id: str,
    tenant_id: str,
    goal: str,
    phone_number: str,
    disposition: str,
    ctx: Any = None,
) -> TaskReport:
    """
    Build a TaskReport for a call that never connected (busy, no-answer, failed, cancelled).

    No conversation is recorded and no LLM assessment is run — the telephony
    outcome is self-explanatory.
    """
    _DISPOSITION_SUMMARIES = {
        "busy":      "The call could not be completed because the line was busy.",
        "no-answer": "The call rang but was not answered.",
        "failed":    "The call failed to connect due to a carrier or routing error.",
        "cancelled": "The call was cancelled before it was answered.",
    }

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    summary = _DISPOSITION_SUMMARIES.get(disposition, f"Call ended with status: {disposition}.")

    agent_name = getattr(ctx, "agent_name", None) if ctx else None
    agent_role = getattr(ctx, "agent_role", "a professional assistant") if ctx else "a professional assistant"
    agent_tone = getattr(ctx, "agent_tone", "friendly and concise") if ctx else "friendly and concise"

    return TaskReport(
        report_id=uuid.uuid4().hex[:16],
        generated_at=now,
        call_id=call_id,
        tenant_id=tenant_id,
        goal=goal,
        agent_name=agent_name,
        agent_role=agent_role,
        agent_tone=agent_tone,
        success_criteria=getattr(ctx, "success_criteria", None) if ctx else None,
        constraints=list(getattr(ctx, "constraints", [])) if ctx else [],
        caller_name=getattr(ctx, "caller_name", None) if ctx else None,
        caller_context=getattr(ctx, "caller_context", None) if ctx else None,
        call_disposition=disposition,
        conversation=[],
        transport=CallTransport(
            call_id=call_id,
            phone_number=phone_number,
            started_at=now,
            ended_at=now,
            duration_s=0.0,
            total_turns=0,
            barge_in_count=0,
            hold_count=0,
            ivr_navigation=IVRNavigation(),
        ),
        performance={},
        goal_achieved=False,
        outcome_summary=summary,
    )


# =============================================================================
# LLM OUTCOME ASSESSMENT
# =============================================================================

async def _assess_goal(
    goal: str,
    success_criteria: Optional[str],
    conversation: List[ConversationTurn],
) -> tuple:
    """
    Use Groq to evaluate whether the call goal was achieved.

    Returns (goal_achieved: Optional[bool], summary: str).
    goal_achieved is None when the evidence is ambiguous or insufficient.
    """
    import os

    lines: List[str] = []
    for turn in conversation:
        # Skip internal control tokens from the transcript
        if turn.speaker_text and not turn.speaker_text.startswith("["):
            lines.append(f"Callee: {turn.speaker_text}")
        if turn.dtmf_pressed:
            lines.append(f"[IVR: pressed {turn.dtmf_pressed!r}]")
        # Filter out hold-check and other internal LLM routing tokens
        if turn.agent_text and not turn.agent_text.startswith("[HOLD_"):
            lines.append(f"Agent: {turn.agent_text}")

    transcript = "\n".join(lines) if lines else "(no conversation recorded)"
    criteria_clause = f"\nSuccess criteria: {success_criteria}" if success_criteria else ""

    prompt = (
        "You are evaluating whether an AI phone agent achieved its stated goal.\n\n"
        f"Goal: {goal}{criteria_clause}\n\n"
        f"Conversation transcript:\n{transcript}\n\n"
        "Respond ONLY in JSON with exactly two fields:\n"
        '{"goal_achieved": true | false | null, '
        '"summary": "2-3 sentences describing what happened and whether the goal was met"}\n\n'
        "Set goal_achieved to null if the transcript is too short or ambiguous to determine."
    )

    from groq import AsyncGroq
    client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY", ""))
    model = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")

    resp = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=256,
        response_format={"type": "json_object"},
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    return data.get("goal_achieved"), data.get("summary", "")
