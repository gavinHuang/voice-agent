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


_calls: Dict[str, ActiveCall] = {}

# Pending call data keyed by Twilio call SID — set when a call is triggered via
# the dashboard UI, consumed when the WebSocket stream_start event arrives.
_pending: Dict[str, Dict[str, str]] = {}


def set_pending(call_sid: str, phone: str, goal: str) -> None:
    _pending[call_sid] = {"phone": phone, "goal": goal}


def pop_pending(call_sid: str) -> Dict[str, str]:
    """Return {phone, goal} for the call SID, or empty strings if not found."""
    return _pending.pop(call_sid, {"phone": "", "goal": ""})


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


def all_calls() -> List[ActiveCall]:
    return list(_calls.values())
