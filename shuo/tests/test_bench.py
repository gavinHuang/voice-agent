"""Unit tests for BENCH-01 (scenario loading) and BENCH-03 (criteria evaluation)."""
import pytest
from shuo.bench import (
    ScenarioConfig,
    SuccessCriteria,
    CriteriaResult,
    ScenarioResult,
    load_scenarios,
    evaluate_criteria,
)


# =============================================================================
# Scenario loading (BENCH-01)
# =============================================================================

def test_load_scenarios(tmp_path):
    """load_scenarios returns list[ScenarioConfig] with correct fields."""
    yaml_file = tmp_path / "scenarios.yaml"
    yaml_file.write_text(
        "scenarios:\n"
        "  - id: test-scenario\n"
        "    description: A test scenario\n"
        "    agent:\n"
        "      goal: Test goal\n"
        "      identity: Customer\n"
        "    timeout: 30\n"
        "    success_criteria:\n"
        "      transcript_contains:\n"
        "        - sales\n"
        "      dtmf_sequence: '1'\n"
        "      max_turns: 5\n"
    )
    results = load_scenarios(str(yaml_file))
    assert isinstance(results, list)
    assert len(results) == 1
    s = results[0]
    assert isinstance(s, ScenarioConfig)
    assert s.id == "test-scenario"
    assert s.description == "A test scenario"
    assert s.agent == {"goal": "Test goal", "identity": "Customer"}
    assert s.timeout == 30
    assert isinstance(s.success_criteria, SuccessCriteria)
    assert s.success_criteria.transcript_contains == ["sales"]
    assert s.success_criteria.dtmf_sequence == "1"
    assert s.success_criteria.max_turns == 5


def test_load_scenarios_invalid(tmp_path):
    """load_scenarios raises ValueError when required field 'id' is missing."""
    yaml_file = tmp_path / "bad_scenarios.yaml"
    yaml_file.write_text(
        "scenarios:\n"
        "  - description: Missing id field\n"
        "    agent:\n"
        "      goal: Test goal\n"
        "      identity: Customer\n"
        "    success_criteria: {}\n"
    )
    with pytest.raises(ValueError, match="id"):
        load_scenarios(str(yaml_file))


def test_scenario_ivr_flow_default(tmp_path):
    """ScenarioConfig with no ivr_flow field has ivr_flow == None."""
    yaml_file = tmp_path / "scenarios.yaml"
    yaml_file.write_text(
        "scenarios:\n"
        "  - id: no-flow\n"
        "    description: No ivr_flow specified\n"
        "    agent:\n"
        "      goal: Test\n"
        "      identity: Tester\n"
        "    success_criteria: {}\n"
    )
    results = load_scenarios(str(yaml_file))
    assert results[0].ivr_flow is None


# =============================================================================
# Criteria evaluation (BENCH-03)
# =============================================================================

def test_criterion_transcript_contains():
    """transcript_contains passes when all strings appear in transcript."""
    result = evaluate_criteria(
        SuccessCriteria(transcript_contains=["sales"]),
        transcript=["Thank you", "sales department"],
        dtmf_log=[],
        turns=2,
    )
    assert isinstance(result, CriteriaResult)
    assert result.transcript_pass is True


def test_criterion_transcript_contains_fail():
    """transcript_contains fails when a string is not in transcript."""
    result = evaluate_criteria(
        SuccessCriteria(transcript_contains=["billing"]),
        transcript=["Thank you", "sales department"],
        dtmf_log=[],
        turns=2,
    )
    assert result.transcript_pass is False


def test_criterion_dtmf_sequence():
    """dtmf_sequence passes when collected digits match exactly."""
    result = evaluate_criteria(
        SuccessCriteria(dtmf_sequence="1"),
        transcript=[],
        dtmf_log=["1"],
        turns=1,
    )
    assert result.dtmf_pass is True


def test_criterion_dtmf_sequence_fail():
    """dtmf_sequence fails when collected digits don't match."""
    result = evaluate_criteria(
        SuccessCriteria(dtmf_sequence="1"),
        transcript=[],
        dtmf_log=["2"],
        turns=1,
    )
    assert result.dtmf_pass is False


def test_criterion_max_turns_exceeded():
    """max_turns fails when turn count exceeds limit."""
    result = evaluate_criteria(
        SuccessCriteria(max_turns=3),
        transcript=[],
        dtmf_log=[],
        turns=5,
    )
    assert result.turns_pass is False


def test_criterion_max_turns_ok():
    """max_turns passes when turn count is within limit."""
    result = evaluate_criteria(
        SuccessCriteria(max_turns=3),
        transcript=[],
        dtmf_log=[],
        turns=2,
    )
    assert result.turns_pass is True


def test_all_criteria_and():
    """evaluate_criteria returns passed=False when any criterion fails (AND logic)."""
    result = evaluate_criteria(
        SuccessCriteria(transcript_contains=["sales"], dtmf_sequence="1"),
        transcript=["Thank you", "sales department"],
        dtmf_log=["2"],  # dtmf fails
        turns=2,
    )
    assert result.transcript_pass is True
    assert result.dtmf_pass is False
    assert result.passed is False


def test_criteria_none_vacuous():
    """evaluate_criteria with all None/empty criteria returns passed=True."""
    result = evaluate_criteria(
        SuccessCriteria(),
        transcript=[],
        dtmf_log=[],
        turns=0,
    )
    assert result.passed is True
    assert result.transcript_pass is True
    assert result.dtmf_pass is True
    assert result.turns_pass is True
