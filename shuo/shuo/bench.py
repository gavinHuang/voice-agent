"""Benchmark data model: scenario loading and success criteria evaluation.

Exports: SuccessCriteria, ScenarioConfig, CriteriaResult, ScenarioResult,
         load_scenarios, evaluate_criteria
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SuccessCriteria:
    """Criteria that must all pass for a scenario to be considered successful."""
    transcript_contains: list[str] = field(default_factory=list)
    dtmf_sequence: Optional[str] = None
    max_turns: Optional[int] = None


@dataclass
class ScenarioConfig:
    """Configuration for a single benchmark scenario."""
    id: str
    description: str
    agent: dict                    # {"goal": str, "identity": str}
    success_criteria: SuccessCriteria
    timeout: int = 30
    ivr_flow: Optional[str] = None


@dataclass
class CriteriaResult:
    """Outcome of evaluating all success criteria for a completed scenario run."""
    transcript_pass: bool
    dtmf_pass: bool
    turns_pass: bool
    passed: bool                   # transcript_pass AND dtmf_pass AND turns_pass


@dataclass
class ScenarioResult:
    """Full result record for a single scenario run."""
    scenario_id: str
    passed: bool
    criteria: CriteriaResult
    turns: int
    dtmf_log: list[str]
    transcript: list[str]
    wall_clock_s: float
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Scenario loading (BENCH-01)
# ---------------------------------------------------------------------------

def load_scenarios(path: str) -> list[ScenarioConfig]:
    """Load scenarios from a YAML file and return typed ScenarioConfig objects.

    Args:
        path: Path to the YAML file containing a top-level ``scenarios:`` list.

    Returns:
        A list of :class:`ScenarioConfig` objects.

    Raises:
        ValueError: If a scenario is missing the required ``id`` field.
    """
    with open(path) as fh:
        data = yaml.safe_load(fh)

    scenarios: list[ScenarioConfig] = []
    for raw in data["scenarios"]:
        if "id" not in raw:
            raise ValueError(
                f"Scenario is missing required field 'id'. "
                f"Found fields: {list(raw.keys())}"
            )
        criteria_raw = raw.get("success_criteria") or {}
        criteria = SuccessCriteria(
            transcript_contains=criteria_raw.get("transcript_contains") or [],
            dtmf_sequence=criteria_raw.get("dtmf_sequence"),
            max_turns=criteria_raw.get("max_turns"),
        )
        scenarios.append(
            ScenarioConfig(
                id=raw["id"],
                description=raw["description"],
                agent=raw["agent"],
                success_criteria=criteria,
                timeout=raw.get("timeout", 30),
                ivr_flow=raw.get("ivr_flow"),
            )
        )
    return scenarios


# ---------------------------------------------------------------------------
# Criteria evaluation (BENCH-03)
# ---------------------------------------------------------------------------

def evaluate_criteria(
    criteria: SuccessCriteria,
    transcript: list[str],
    dtmf_log: list[str],
    turns: int,
) -> CriteriaResult:
    """Evaluate success criteria against the collected run data.

    Args:
        criteria: The :class:`SuccessCriteria` from the scenario config.
        transcript: List of transcript strings collected during the run.
        dtmf_log: List of individual DTMF digit strings pressed during the run.
        turns: Total number of conversation turns completed.

    Returns:
        A :class:`CriteriaResult` with per-criterion pass/fail and an overall
        ``passed`` flag (AND of all criteria).
    """
    # transcript_pass: vacuously True when list is empty;
    # otherwise ALL strings must appear (case-insensitive) in joined transcript.
    if not criteria.transcript_contains:
        transcript_pass = True
    else:
        full_text = " ".join(transcript).lower()
        transcript_pass = all(s.lower() in full_text for s in criteria.transcript_contains)

    # dtmf_pass: vacuously True when dtmf_sequence is None;
    # otherwise joined dtmf_log must equal dtmf_sequence exactly.
    if criteria.dtmf_sequence is None:
        dtmf_pass = True
    else:
        dtmf_pass = "".join(dtmf_log) == criteria.dtmf_sequence

    # turns_pass: vacuously True when max_turns is None;
    # otherwise actual turns must not exceed the limit.
    if criteria.max_turns is None:
        turns_pass = True
    else:
        turns_pass = turns <= criteria.max_turns

    return CriteriaResult(
        transcript_pass=transcript_pass,
        dtmf_pass=dtmf_pass,
        turns_pass=turns_pass,
        passed=transcript_pass and dtmf_pass and turns_pass,
    )
