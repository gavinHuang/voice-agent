"""
IVR Navigator: systematically explores a phone IVR system to map its full menu tree.

Makes repeated calls to a target phone number, each navigating a specific path
in the menu hierarchy. Treats the phone call as a blackbox — the navigator only
registers a per-call observer, supplies a goal, and analyzes the resulting IVR
transcript to discover options.

The navigator uses BFS to explore the tree:
  - Root probe: call the number, listen to all options without pressing anything
  - Path probe: call the number, navigate digits in sequence, listen at destination

Navigation strategies used:
  - Fresh call per probe (hangup and recall) — always starts from root
  - DTMF digits for in-call navigation to reach sub-menus

Usage:
    from shuo.ivr_navigator import IVRNavigator, format_tree

    navigator = IVRNavigator(phone_number="+61300000000")
    tree = await navigator.explore()
    print(format_tree(tree))
    print(json.dumps(tree.to_dict(), indent=2))
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# TREE DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

_AGENT_WORDS     = ("agent", "representative", "operator", "speak to", "speak with",
                    "customer service", "consultant", "advisor")
_VOICEMAIL_WORDS = ("voicemail", "leave a message", "leave a voicemail", "record a message")
_HANGUP_WORDS    = ("goodbye", "good bye", "end call", "terminate", "no further")
_INFO_WORDS      = ("hours", "address", "location", "information about", "find out",
                    "hear about", "office hours")


@dataclass
class MenuNode:
    """One node in the IVR menu tree."""
    key: str                          # Digit pressed to reach this node ("" = root)
    label: str                        # IVR's description of this option
    path: Tuple[str, ...]             # Path of digits from root
    children: List[MenuNode] = field(default_factory=list)
    terminal: Optional[str] = None    # None=submenu; "agent"|"voicemail"|"hangup"|"info"|"unknown"

    def add_child(self, child: MenuNode) -> None:
        self.children.append(child)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "path": list(self.path),
            "terminal": self.terminal,
            "children": [c.to_dict() for c in self.children],
        }

    def format(self, indent: int = 0) -> str:
        """Render this subtree as an indented text tree."""
        pad = "  " * indent
        badge = f"[{self.key}]" if self.key else "[ROOT]"
        term_str = f"  → {self.terminal.upper()}" if self.terminal else ""
        lines = [f"{pad}{badge} {self.label}{term_str}"]
        for child in sorted(self.children, key=lambda c: c.key):
            lines.append(child.format(indent + 1))
        return "\n".join(lines)


@dataclass
class _ProbeResult:
    path: Tuple[str, ...]
    options: List[Tuple[str, str]]    # [(digit, label), ...]
    terminal: Optional[str] = None    # Set if path ended at a non-menu state
    raw_transcript: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# PROBE GOAL GENERATION
# ─────────────────────────────────────────────────────────────────────────────

def _probe_goal(path: Tuple[str, ...]) -> str:
    """Return the agent goal string for probing a specific IVR path."""
    if not path:
        return (
            "IVR probe. Listen silently. No speech, no DTMF. "
            "When menu options finish, call signal_hangup() with no text."
        )

    steps = ", ".join(f"press '{d}'" for d in path)
    return (
        f"IVR probe. Navigate: {steps} (one digit per menu level). "
        "At the final menu listen to all options without pressing more. "
        "Then call signal_hangup() with no text."
    )


# ─────────────────────────────────────────────────────────────────────────────
# TRANSCRIPT ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

async def _analyze_transcript(transcript: str, path: Tuple[str, ...]) -> _ProbeResult:
    """Use an LLM to extract IVR menu options from the collected transcript."""
    from groq import AsyncGroq

    if not transcript.strip():
        log.warning(f"Empty transcript for path {list(path) or ['ROOT']}")
        return _ProbeResult(path=path, options=[], terminal="unknown", raw_transcript=transcript)

    client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
    model = os.getenv("LLM_MODEL", "groq:llama-3.3-70b-versatile").removeprefix("groq:")
    path_desc = f"after navigating: {' → '.join(path)}" if path else "at the root level"

    prompt = f"""You are analyzing a transcript from an IVR (Interactive Voice Response) system.
The recording is {path_desc}.

IVR transcript:
---
{transcript}
---

Extract the menu structure. Return a JSON object with:
- "options": array of {{"digit": "N", "label": "brief description"}} for each menu option
- "terminal": null if this is a menu with options, or one of:
  - "agent" if caller is being/was transferred to a human agent
  - "voicemail" if caller reached or left a voicemail
  - "hangup" if call ended without menu
  - "info" if only information was provided, no selections available

Examples:
- "Press 1 for billing, press 2 for technical support" → {{"options": [{{"digit": "1", "label": "billing"}}, {{"digit": "2", "label": "technical support"}}], "terminal": null}}
- "Transferring you to an agent, please hold" → {{"options": [], "terminal": "agent"}}
- "Our offices are open Monday to Friday 9am to 5pm" → {{"options": [], "terminal": "info"}}

Return ONLY valid JSON, no explanation or markdown fences."""

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.0,
        )
        content = response.choices[0].message.content.strip()
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            data = json.loads(match.group())
            options = [
                (str(o.get("digit", "")), str(o.get("label", "")))
                for o in data.get("options", [])
                if o.get("digit") and o.get("label")
            ]
            terminal = data.get("terminal") or None
            return _ProbeResult(path=path, options=options, terminal=terminal,
                                raw_transcript=transcript)
    except Exception as exc:
        log.warning(f"Transcript analysis failed for path {list(path) or ['ROOT']}: {exc}")

    return _ProbeResult(path=path, options=[], terminal="unknown", raw_transcript=transcript)


def _guess_terminal(label: str) -> Optional[str]:
    """Heuristic: does this label describe a terminal state (no sub-menu)?"""
    lower = label.lower()
    if any(w in lower for w in _AGENT_WORDS):
        return "agent"
    if any(w in lower for w in _VOICEMAIL_WORDS):
        return "voicemail"
    if any(w in lower for w in _HANGUP_WORDS):
        return "hangup"
    if any(w in lower for w in _INFO_WORDS):
        return "info"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# NAVIGATOR
# ─────────────────────────────────────────────────────────────────────────────

class IVRNavigator:
    """
    Explores a phone IVR system by making repeated targeted calls.

    Treats phone calls as a blackbox: supplies a probe goal and reads
    collected IVR transcripts via an observer. An LLM extracts menu options
    from each transcript. BFS walks the discovered tree.

    Each probe call:
      1. Connects to the IVR from scratch (fresh call = always starts at root)
      2. Navigates a specific digit-path via DTMF
      3. Listens to all options at the destination level
      4. Hangs up

    Requires the voice-agent server to be running (handles Twilio webhooks).

    Args:
        phone_number:  E.164 phone number to explore (e.g. "+61300000000")
        max_depth:     Maximum menu nesting depth to explore (default: 5)
        max_calls:     Hard limit on total calls made (default: 30)
        call_timeout:  Seconds to wait for each probe call (default: 90)
    """

    def __init__(
        self,
        phone_number: str,
        max_depth: int = 5,
        max_calls: int = 30,
        call_timeout: int = 90,
    ):
        self.phone_number = phone_number
        self.max_depth = max_depth
        self.max_calls = max_calls
        self.call_timeout = call_timeout
        self._calls_made = 0

    async def explore(self) -> MenuNode:
        """
        Explore the full IVR tree. Returns the root MenuNode with all discovered
        children populated.
        """
        root = MenuNode(key="", label=self.phone_number, path=())

        # BFS queue: (path_to_explore, parent_node)
        queue: deque[Tuple[Tuple[str, ...], MenuNode]] = deque()
        queue.append(((), root))
        explored: set[Tuple[str, ...]] = set()

        while queue and self._calls_made < self.max_calls:
            path, parent = queue.popleft()

            if path in explored:
                continue
            explored.add(path)

            if len(path) >= self.max_depth:
                log.info(f"Max depth {self.max_depth} reached at path {list(path)}")
                continue

            path_str = " → ".join(path) if path else "ROOT"
            log.info(
                f"[Call {self._calls_made + 1}/{self.max_calls}] "
                f"Probing: {path_str}"
            )

            try:
                result = await self._probe_path(path)
            except Exception as exc:
                log.error(f"Probe failed for {path_str}: {exc}")
                continue

            if result.terminal:
                parent.terminal = result.terminal
                log.info(f"  Terminal at {path_str}: {result.terminal}")
                continue

            if not result.options:
                log.warning(f"  No options discovered at {path_str}")
                parent.terminal = "unknown"
                continue

            log.info(f"  Found {len(result.options)} option(s) at {path_str}")
            for digit, label in result.options:
                child_path = path + (digit,)
                child = MenuNode(key=digit, label=label, path=child_path)
                parent.add_child(child)

                terminal_guess = _guess_terminal(label)
                if terminal_guess:
                    child.terminal = terminal_guess
                    log.info(f"    [{digit}] {label}  (guessed: {terminal_guess})")
                elif child_path not in explored:
                    queue.append((child_path, child))
                    log.info(f"    [{digit}] {label}  (will probe)")
                else:
                    log.info(f"    [{digit}] {label}  (already explored)")

        if self._calls_made >= self.max_calls and queue:
            log.warning(
                f"Reached max_calls={self.max_calls} — "
                f"{len(queue)} path(s) not explored"
            )

        return root

    async def _probe_path(self, path: Tuple[str, ...]) -> _ProbeResult:
        """
        Make one call, navigate to `path` via DTMF, collect the menu transcript.
        """
        import shuo.web as _web_module
        from shuo.phone import dial_out
        from monitor import registry as dashboard_registry

        transcript_lines: List[str] = []
        done_event = threading.Event()

        def nav_observer(event: dict) -> None:
            etype = event.get("type")
            if etype == "transcript":
                text = event.get("text", "").strip()
                if text:
                    transcript_lines.append(text)
            elif etype == "stream_stop":
                done_event.set()

        goal = _probe_goal(path)

        # dial_out() is a blocking Twilio REST call — run in executor
        loop = asyncio.get_event_loop()
        call_sid = await loop.run_in_executor(
            None, lambda: dial_out(self.phone_number, ivr_mode=True)
        )
        self._calls_made += 1

        # Register goal and observer AFTER obtaining call_sid (Twilio has latency
        # before the WebSocket connects, so there is no race condition here)
        dashboard_registry.set_pending(call_sid, self.phone_number, goal, ivr_mode=True)
        _web_module._navigator_observers[call_sid] = nav_observer

        try:
            timed_out = await loop.run_in_executor(
                None, lambda: not done_event.wait(timeout=self.call_timeout)
            )
            if timed_out:
                log.warning(
                    f"Probe timed out after {self.call_timeout}s "
                    f"for path {list(path) or ['ROOT']} — analyzing partial transcript"
                )
        finally:
            _web_module._navigator_observers.pop(call_sid, None)

        raw = "\n".join(transcript_lines)
        log.debug(f"Transcript for {list(path) or ['ROOT']}:\n{raw or '(empty)'}")

        return await _analyze_transcript(raw, path)


# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT FORMATTING
# ─────────────────────────────────────────────────────────────────────────────

def format_tree(root: MenuNode, phone: str = "") -> str:
    """Return a human-readable formatted string of the full IVR menu tree."""
    phone_str = phone or root.label
    lines = [
        "=" * 60,
        f"  IVR Menu Tree: {phone_str}",
        "=" * 60,
        "",
        root.format(),
        "",
        "=" * 60,
    ]
    return "\n".join(lines)
