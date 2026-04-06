"""
context.py — CallContext: typed call parameters and pre-call confirmation.

Provides:
  CallContext           — dataclass of all agent/call fields
  load_identity_file()  — discover and parse ~/identity.md or <cwd>/identity.md
  build_system_prompt() — assemble the goal/persona portion of the system prompt
  confirm_context()     — interactive pre-call confirmation gate
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List

import yaml


# =============================================================================
# CALL CONTEXT
# =============================================================================

@dataclass
class CallContext:
    """
    Typed parameters for a single outbound call.

    Required: goal (must be a non-empty string — raises ValueError if empty/None)
    Optional: all others (defaults reflect the built-in "Alex" persona)
    """
    goal: str
    agent_name: str = "Alex"
    agent_role: str = "a professional assistant"
    agent_tone: str = "friendly and concise"
    agent_background: Optional[str] = None   # free-text from identity.md body
    caller_name: Optional[str] = None
    caller_context: Optional[str] = None
    constraints: List[str] = field(default_factory=list)
    success_criteria: Optional[str] = None

    def __post_init__(self):
        if not self.goal:
            raise ValueError("CallContext: 'goal' is required and cannot be empty")

    @classmethod
    def _partial(cls, **kwargs) -> "CallContext":
        """
        Construct a CallContext bypassing __post_init__ validation.
        Used by the CLI when goal is not yet known (will be filled by confirm_context).
        """
        obj = object.__new__(cls)
        defaults = {
            "goal": "",
            "agent_name": "Alex",
            "agent_role": "a professional assistant",
            "agent_tone": "friendly and concise",
            "agent_background": None,
            "caller_name": None,
            "caller_context": None,
            "constraints": [],
            "success_criteria": None,
        }
        defaults.update(kwargs)
        obj.__dict__.update(defaults)
        return obj

    # ── Serialization ────────────────────────────────────────────────

    @classmethod
    def from_yaml(cls, path: str | Path) -> "CallContext":
        """Load a CallContext from a YAML file. Missing optional fields use defaults."""
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(
            goal=data.get("goal", ""),
            agent_name=data.get("agent_name", "Alex"),
            agent_role=data.get("agent_role", "a professional assistant"),
            agent_tone=data.get("agent_tone", "friendly and concise"),
            agent_background=data.get("agent_background"),
            caller_name=data.get("caller_name"),
            caller_context=data.get("caller_context"),
            constraints=list(data.get("constraints") or []),
            success_criteria=data.get("success_criteria"),
        )

    def to_yaml(self, path: str | Path) -> None:
        """Serialize this CallContext to a YAML file."""
        data: dict = {"goal": self.goal}
        if self.agent_name != "Alex":
            data["agent_name"] = self.agent_name
        if self.agent_role != "a professional assistant":
            data["agent_role"] = self.agent_role
        if self.agent_tone != "friendly and concise":
            data["agent_tone"] = self.agent_tone
        if self.agent_background:
            data["agent_background"] = self.agent_background
        if self.caller_name:
            data["caller_name"] = self.caller_name
        if self.caller_context:
            data["caller_context"] = self.caller_context
        if self.constraints:
            data["constraints"] = list(self.constraints)
        if self.success_criteria:
            data["success_criteria"] = self.success_criteria
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


# =============================================================================
# IDENTITY FILE LOADING
# =============================================================================

def load_identity_file(cwd: Path) -> tuple[dict, str]:
    """
    Discover and parse an identity.md file.

    Search order:
      1. <cwd>/identity.md   (project-local persona)
      2. ~/identity.md        (user-global persona)

    Returns (fields_dict, source_label) where fields_dict contains any subset
    of: agent_name, agent_role, agent_tone, agent_background.
    source_label is a human-readable path string, or "" if not found.
    """
    candidates = [
        (cwd / "identity.md",       "identity.md"),
        (Path.home() / "identity.md", "~/identity.md"),
    ]
    for path, label in candidates:
        if path.exists():
            return _parse_identity_file(path), label
    return {}, ""


def _parse_identity_file(path: Path) -> dict:
    """
    Parse identity.md.

    Format:
      Optional YAML front matter (between --- delimiters) for structured fields:
        name:  <agent name>
        role:  <agent role>
        tone:  <agent tone>
      Optional markdown body treated as agent_background.

    If no front matter is present, the entire file content is agent_background.
    """
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return {}

    result: dict = {}

    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", content, re.DOTALL)
    if fm_match:
        front_matter_text = fm_match.group(1)
        body = fm_match.group(2).strip()
        try:
            fm = yaml.safe_load(front_matter_text) or {}
        except yaml.YAMLError:
            fm = {}
        if fm.get("name"):
            result["agent_name"] = str(fm["name"])
        if fm.get("role"):
            result["agent_role"] = str(fm["role"])
        if fm.get("tone"):
            result["agent_tone"] = str(fm["tone"])
        if body:
            result["agent_background"] = body
    else:
        result["agent_background"] = content

    return result


# =============================================================================
# SYSTEM PROMPT ASSEMBLY
# =============================================================================

def build_system_prompt(ctx: CallContext, tools: bool = True) -> str:
    """
    Assemble the goal/persona/context portion of the system prompt.

    This string is appended to the base operational prompt in language.py,
    replacing the old _goal_suffix() call.
    """
    if not ctx.goal:
        return ""

    lines = [
        "Your identity on this call:",
        f"- Name: {ctx.agent_name}",
        f"- Role: {ctx.agent_role}",
        f"- Tone: {ctx.agent_tone}",
    ]

    if ctx.agent_background:
        lines.append(f"\nBackground:\n{ctx.agent_background}")

    lines.append(f"\nYour goal for this call: {ctx.goal}")
    lines.append(
        "Pursue this goal naturally. Do NOT announce your goal — just work towards it. "
        "Once accomplished, confirm details and STOP — wait for their reply. "
        + (
            "Only after they confirm, say goodbye and call signal_hangup() in a separate response."
            if tools else
            "Only after they confirm, say goodbye and emit [HANGUP]."
        )
    )

    if ctx.caller_name:
        lines.append(f"\nYou are calling {ctx.caller_name}.")
    if ctx.caller_context:
        lines.append(f"Context about the caller: {ctx.caller_context}")

    if ctx.constraints:
        lines.append("\nInstructions you must follow:")
        for c in ctx.constraints:
            lines.append(f"- {c}")

    if ctx.success_criteria:
        lines.append(f"\nThis call is successful when: {ctx.success_criteria}")

    ivr_rule = (
        "\nIVR NAVIGATION RULE: When you hear a recorded menu listing options, "
        + ("call press_dtmf() with ONLY the digit — no words, no explanation."
           if tools else
           "emit ONLY the [DTMF:X] tag.")
    )
    lines.append(ivr_rule)

    return "\n".join(lines)


# =============================================================================
# PRE-CALL CONFIRMATION
# =============================================================================

# Editable fields in display order: (label, field_name, is_list)
_EDITABLE_FIELDS = [
    ("Agent name",       "agent_name",       False),
    ("Agent role",       "agent_role",       False),
    ("Agent tone",       "agent_tone",       False),
    ("Goal",             "goal",             False),
    ("Caller name",      "caller_name",      False),
    ("Caller context",   "caller_context",   False),
    ("Constraints",      "constraints",      True),
    ("Success criteria", "success_criteria", False),
]

_ACTION_PROCEED = "Proceed with call"
_ACTION_CANCEL  = "Cancel"


def _render_context(ctx: CallContext, sources: dict) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False,
                  show_edge=False, padding=(0, 1))
    table.add_column("Field", style="dim", min_width=18)
    table.add_column("Value")
    table.add_column("Source", style="dim italic")

    for label, fname, is_list in _EDITABLE_FIELDS:
        value = getattr(ctx, fname)
        src   = sources.get(fname, "")

        if is_list:
            display = ", ".join(value) if value else "[dim](none)[/dim]"
        else:
            display = value if value else "[dim](not set)[/dim]"

        from rich.markup import escape as _escape
        table.add_row(label, display, _escape(f"[{src}]") if src else "")

    if ctx.agent_background:
        preview = ctx.agent_background[:80].replace("\n", " ")
        if len(ctx.agent_background) > 80:
            preview += "…"
        src = sources.get("agent_background", "")
        table.add_row("Agent background", preview, f"[{src}]" if src else "")

    console.print()
    console.rule("[bold]Call Context[/bold]", style="dim")
    console.print(table)
    console.rule(style="dim")
    console.print()


def _build_choices(ctx: CallContext) -> list:
    """Build the questionary choice list: action choices + one choice per field."""
    import questionary

    choices = [
        questionary.Choice(title=_ACTION_PROCEED, value=_ACTION_PROCEED),
        questionary.Choice(title=_ACTION_CANCEL,  value=_ACTION_CANCEL),
        questionary.Separator(),
    ]
    for label, fname, is_list in _EDITABLE_FIELDS:
        value = getattr(ctx, fname)
        if is_list:
            preview = ", ".join(value) if value else "(none)"
        else:
            preview = (value[:50] + "…") if value and len(value) > 50 else (value or "(not set)")
        title = f"Edit  {label:<20}  {preview}"
        choices.append(questionary.Choice(title=title, value=fname))
    return choices


def _edit_field(ctx: CallContext, fname: str, sources: dict) -> CallContext:
    import questionary
    from dataclasses import replace

    label, _, is_list = next(f for f in _EDITABLE_FIELDS if f[1] == fname)
    current = getattr(ctx, fname)

    if is_list:
        current_str = ", ".join(current) if current else ""
        new_str = questionary.text(
            f"{label} (comma-separated, blank to clear):",
            default=current_str,
        ).ask()
        if new_str is None:          # Ctrl-C
            return ctx
        new_val = [s.strip() for s in new_str.split(",") if s.strip()]
        sources.pop(fname, None)
        return replace(ctx, **{fname: new_val})
    else:
        new_val = questionary.text(
            f"{label}:",
            default=current or "",
        ).ask()
        if new_val is None:          # Ctrl-C
            return ctx
        new_val = new_val.strip()
        if fname == "goal" and not new_val:
            print("  Goal cannot be empty.")
            return ctx
        sources.pop(fname, None)
        result = new_val if new_val else None
        return replace(ctx, **{fname: new_val if fname == "goal" else result})


def confirm_context(
    ctx: CallContext,
    yes: bool = False,
    sources: Optional[dict] = None,
) -> CallContext:
    """
    Display the assembled CallContext, prompt for any missing required fields,
    and ask the operator to confirm before dialing.

    Parameters
    ----------
    ctx     : The CallContext to display and confirm (may have empty goal).
    yes     : If True, skip the interactive "Proceed?" prompt.
    sources : Optional dict mapping field names to source labels for annotation,
              e.g. {"agent_name": "identity.md", "agent_role": "identity.md"}.

    Returns the (possibly updated) CallContext with goal filled in.
    Exits the process if the operator declines or if --yes is set with no goal.
    """
    import click
    from dataclasses import replace

    sources = sources or {}

    # ── Prompt for missing required fields ──────────────────────────
    if not ctx.goal:
        if yes:
            click.echo("Error: 'goal' is required but was not provided.", err=True)
            sys.exit(1)
        goal_input = click.prompt("  Call goal (required)").strip()
        if not goal_input:
            click.echo("Error: goal cannot be empty.", err=True)
            sys.exit(1)
        ctx = replace(ctx, goal=goal_input)

    # ── Skip confirmation ────────────────────────────────────────────
    if yes:
        _render_context(ctx, sources)
        return ctx

    # ── Interactive confirm loop ─────────────────────────────────────
    import questionary

    while True:
        _render_context(ctx, sources)

        action = questionary.select(
            "What would you like to do?",
            choices=_build_choices(ctx),
            use_shortcuts=False,
        ).ask()

        if action is None or action == _ACTION_CANCEL:
            click.echo("Call cancelled.")
            sys.exit(0)
        elif action == _ACTION_PROCEED:
            return ctx
        else:
            # action is a field name
            ctx = _edit_field(ctx, action, sources)
