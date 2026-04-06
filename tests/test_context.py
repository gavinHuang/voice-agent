"""
Tests for shuo/context.py — CallContext, identity file loading,
system prompt assembly, and pre-call confirmation.
"""

import sys
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest
import yaml


# =============================================================================
# 5.1  CallContext defaults and required-field validation
# =============================================================================

def test_callcontext_required_field_raises():
    """CallContext raises ValueError when goal is empty."""
    from shuo.context import CallContext
    with pytest.raises(ValueError, match="goal"):
        CallContext(goal="")


def test_callcontext_defaults():
    """Optional fields default to the built-in Alex persona."""
    from shuo.context import CallContext
    ctx = CallContext(goal="Book a table for two")
    assert ctx.agent_name == "Alex"
    assert ctx.agent_role == "a professional assistant"
    assert ctx.agent_tone == "friendly and concise"
    assert ctx.agent_background is None
    assert ctx.caller_name is None
    assert ctx.caller_context is None
    assert ctx.constraints == []
    assert ctx.success_criteria is None


def test_callcontext_full_construction():
    """All fields can be set explicitly."""
    from shuo.context import CallContext
    ctx = CallContext(
        goal="Cancel the reservation",
        agent_name="Jordan",
        agent_role="customer service rep",
        agent_tone="professional",
        agent_background="8 years experience",
        caller_name="Sam",
        caller_context="Premium member",
        constraints=["never offer refunds > $50"],
        success_criteria="Reservation cancelled and confirmed",
    )
    assert ctx.goal == "Cancel the reservation"
    assert ctx.agent_name == "Jordan"
    assert ctx.caller_name == "Sam"
    assert ctx.constraints == ["never offer refunds > $50"]


def test_callcontext_partial_bypasses_validation():
    """_partial() constructs a CallContext with empty goal without raising."""
    from shuo.context import CallContext
    ctx = CallContext._partial(agent_name="Jordan")
    assert ctx.goal == ""
    assert ctx.agent_name == "Jordan"
    assert ctx.agent_role == "a professional assistant"


# =============================================================================
# 5.2  YAML round-trip serialization
# =============================================================================

def test_callcontext_yaml_roundtrip(tmp_path):
    """Fully populated CallContext survives YAML serialization and back."""
    from shuo.context import CallContext
    original = CallContext(
        goal="Get account status",
        agent_name="Jordan",
        agent_role="account manager",
        agent_tone="professional",
        agent_background="Specialises in enterprise accounts",
        caller_name="Sam",
        caller_context="Tier 1 customer",
        constraints=["never quote pricing", "always escalate complaints"],
        success_criteria="Account status confirmed",
    )
    out = tmp_path / "ctx.yaml"
    original.to_yaml(out)
    loaded = CallContext.from_yaml(out)
    assert loaded.goal == original.goal
    assert loaded.agent_name == original.agent_name
    assert loaded.agent_role == original.agent_role
    assert loaded.agent_background == original.agent_background
    assert loaded.caller_name == original.caller_name
    assert loaded.constraints == original.constraints
    assert loaded.success_criteria == original.success_criteria


def test_callcontext_yaml_minimal(tmp_path):
    """YAML with only goal loads and applies defaults for missing optional fields."""
    from shuo.context import CallContext
    p = tmp_path / "min.yaml"
    p.write_text("goal: Confirm appointment\n")
    ctx = CallContext.from_yaml(p)
    assert ctx.goal == "Confirm appointment"
    assert ctx.agent_name == "Alex"
    assert ctx.constraints == []
    assert ctx.caller_name is None


def test_callcontext_yaml_missing_goal_raises(tmp_path):
    """YAML without goal raises ValueError via CallContext constructor."""
    from shuo.context import CallContext
    p = tmp_path / "no_goal.yaml"
    p.write_text("agent_name: Jordan\n")
    with pytest.raises(ValueError, match="goal"):
        CallContext.from_yaml(p)


# =============================================================================
# 5.3  build_system_prompt
# =============================================================================

def test_build_system_prompt_full():
    """Full context: all fields appear in the assembled prompt."""
    from shuo.context import CallContext, build_system_prompt
    ctx = CallContext(
        goal="Get account status",
        agent_name="Jordan",
        agent_role="account manager",
        agent_tone="professional",
        agent_background="Specialises in enterprise accounts",
        caller_name="Sam",
        caller_context="Tier 1 customer",
        constraints=["never quote pricing"],
        success_criteria="Account status confirmed",
    )
    prompt = build_system_prompt(ctx)
    assert "Jordan" in prompt
    assert "account manager" in prompt
    assert "Get account status" in prompt
    assert "Sam" in prompt
    assert "Tier 1 customer" in prompt
    assert "never quote pricing" in prompt
    assert "Account status confirmed" in prompt
    assert "Specialises in enterprise accounts" in prompt


def test_build_system_prompt_minimal():
    """Minimal context: Alex defaults appear, no placeholder text."""
    from shuo.context import CallContext, build_system_prompt
    ctx = CallContext(goal="Book a table for two")
    prompt = build_system_prompt(ctx)
    assert "Alex" in prompt
    assert "a professional assistant" in prompt
    assert "Book a table for two" in prompt
    # No placeholder text
    assert "[UNKNOWN]" not in prompt
    assert "N/A" not in prompt


def test_build_system_prompt_empty_goal_returns_empty():
    """Empty goal produces an empty string (no fabricated content)."""
    from shuo.context import CallContext, build_system_prompt
    ctx = CallContext._partial()
    result = build_system_prompt(ctx)
    assert result == ""


def test_build_system_prompt_tools_flag():
    """tools=False uses text-tag protocol references instead of function calls."""
    from shuo.context import CallContext, build_system_prompt
    ctx = CallContext(goal="Do something")
    prompt_tools = build_system_prompt(ctx, tools=True)
    prompt_tags  = build_system_prompt(ctx, tools=False)
    assert "signal_hangup()" in prompt_tools
    assert "[HANGUP]" in prompt_tags


# =============================================================================
# 5.4  confirm_context
# =============================================================================

def test_confirm_context_yes_flag_skips_prompt():
    """--yes flag returns the context without prompting."""
    from shuo.context import CallContext, confirm_context
    ctx = CallContext(goal="Book a table")
    result = confirm_context(ctx, yes=True)
    assert result.goal == "Book a table"


def test_confirm_context_yes_with_empty_goal_exits(capsys):
    """--yes with missing goal exits with code 1."""
    from shuo.context import CallContext, confirm_context
    ctx = CallContext._partial()
    with pytest.raises(SystemExit) as exc_info:
        confirm_context(ctx, yes=True)
    assert exc_info.value.code == 1


def test_confirm_context_displays_not_set_for_absent_optionals(capsys):
    """Optional fields that are None show as (not set) in the summary."""
    from shuo.context import CallContext, confirm_context
    ctx = CallContext(goal="Check balance")
    confirm_context(ctx, yes=True)
    captured = capsys.readouterr()
    assert "(not set)" in captured.out


def test_confirm_context_source_annotations(capsys):
    """Source labels appear next to annotated fields."""
    from shuo.context import CallContext, confirm_context
    ctx = CallContext(goal="Check balance", agent_name="Jordan")
    confirm_context(ctx, yes=True, sources={"agent_name": "identity.md"})
    captured = capsys.readouterr()
    assert "identity.md" in captured.out


def test_confirm_context_negative_answer_exits(monkeypatch):
    """Pressing Enter (empty input) at 'Proceed?' aborts with exit code 0."""
    from shuo.context import CallContext, confirm_context
    import click
    monkeypatch.setattr("click.prompt", lambda *a, **kw: "")
    ctx = CallContext(goal="Book a table")
    with pytest.raises(SystemExit) as exc_info:
        confirm_context(ctx, yes=False)
    assert exc_info.value.code == 0


def test_confirm_context_affirmative_returns_ctx(monkeypatch):
    """Entering 'y' returns the confirmed context."""
    from shuo.context import CallContext, confirm_context
    monkeypatch.setattr("click.prompt", lambda *a, **kw: "y")
    ctx = CallContext(goal="Book a table")
    result = confirm_context(ctx, yes=False)
    assert result.goal == "Book a table"


# =============================================================================
# 5.5  Identity file loading
# =============================================================================

def test_load_identity_file_not_found(tmp_path):
    """Returns empty dict and empty label when no identity.md exists."""
    from shuo.context import load_identity_file
    fields, label = load_identity_file(tmp_path)
    assert fields == {}
    assert label == ""


def test_load_identity_file_project_local(tmp_path):
    """Finds <cwd>/identity.md and parses front matter."""
    from shuo.context import load_identity_file
    (tmp_path / "identity.md").write_text(textwrap.dedent("""\
        ---
        name: Jordan
        role: senior account manager
        tone: professional
        ---
        Jordan has 8 years of enterprise sales experience.
    """))
    fields, label = load_identity_file(tmp_path)
    assert fields["agent_name"] == "Jordan"
    assert fields["agent_role"] == "senior account manager"
    assert fields["agent_tone"] == "professional"
    assert "8 years" in fields["agent_background"]
    assert label == "identity.md"


def test_load_identity_file_body_only(tmp_path):
    """File with no front matter is treated entirely as agent_background."""
    from shuo.context import load_identity_file
    (tmp_path / "identity.md").write_text("I am a helpful assistant with broad knowledge.")
    fields, label = load_identity_file(tmp_path)
    assert "agent_background" in fields
    assert "agent_name" not in fields


def test_load_identity_file_prefers_project_local(tmp_path, monkeypatch):
    """Project-local identity.md takes precedence over ~/identity.md."""
    from shuo.context import load_identity_file
    # Write a project-local identity.md
    (tmp_path / "identity.md").write_text("---\nname: Local\n---\n")
    # Patch Path.home() to point to a different tmp dir (no identity.md there)
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    (home_dir / "identity.md").write_text("---\nname: Global\n---\n")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home_dir))
    fields, label = load_identity_file(tmp_path)
    assert fields["agent_name"] == "Local"
    assert label == "identity.md"
