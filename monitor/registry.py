"""
Active call registry for the monitoring dashboard.

Stores per-call state (mode, agent ref, phone) so the dashboard server
can look up calls for control operations (hangup, takeover, DTMF).
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class CallMode(Enum):
    AGENT    = "agent"     # Normal — agent controls the conversation
    TAKEOVER = "takeover"  # Human supervisor has taken over


@dataclass
class ActiveCall:
    call_id:    str
    call_sid:   str = ""      # Twilio call SID (CA...) for REST API
    phone:      str = ""      # Remote phone number
    goal:       str = ""      # Call goal / purpose shown in dashboard
    mode:       CallMode = CallMode.AGENT
    agent:      Any = None    # shuo Agent instance (for DTMF injection)
    started_at: float = field(default_factory=time.monotonic)
    tenant_id:  str = "default"   # Tenant that owns this call
    # Take-over state preservation
    saved_history:       List[Dict[str, str]] = field(default_factory=list)
    takeover_transcript: List[str] = field(default_factory=list)
    listen_stream_sid:   str = ""
    softphone_call_sid:  str = ""


_calls: Dict[str, ActiveCall] = {}

# Pending call data keyed by Twilio call SID — set when a call is triggered via
# the dashboard UI, consumed when the WebSocket stream_start event arrives.
_pending: Dict[str, Dict[str, str]] = {}


def set_pending(
    call_sid: str,
    phone: str,
    goal: str,
    ivr_mode: bool = False,
    tenant_id: str = "default",
) -> None:
    _pending[call_sid] = {
        "phone": phone,
        "goal": goal,
        "ivr_mode": ivr_mode,
        "tenant_id": tenant_id,
    }


def pop_pending(call_sid: str) -> Dict:
    """Return {phone, goal, ivr_mode, tenant_id} for the call SID, or defaults."""
    return _pending.pop(
        call_sid,
        {"phone": "", "goal": "", "ivr_mode": False, "tenant_id": "default"},
    )


def register(call: ActiveCall) -> None:
    _calls[call.call_id] = call


def get(call_id: str) -> Optional[ActiveCall]:
    return _calls.get(call_id)


def update(call_id: str, **kwargs) -> None:
    call = _calls.get(call_id)
    if call:
        for k, v in kwargs.items():
            setattr(call, k, v)


def remove(call_id: str) -> None:
    _calls.pop(call_id, None)


def find_by_call_sid(call_sid: str) -> Optional[ActiveCall]:
    """Look up a call by its Twilio call SID."""
    for call in _calls.values():
        if call.call_sid == call_sid:
            return call
    return None


def all_calls() -> List[ActiveCall]:
    return list(_calls.values())


def calls_for_tenant(tenant_id: str) -> List[ActiveCall]:
    """Return only calls that belong to the given tenant."""
    return [c for c in _calls.values() if c.tenant_id == tenant_id]
