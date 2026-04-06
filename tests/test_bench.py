"""Unit tests for BENCH-01 (scenario loading), BENCH-03 (criteria evaluation),
BENCH-02 and BENCH-04 (IVRDriver, BenchISP, run_scenario, run_benchmark).
BENCH-05 (e2e: sample scenarios pass against IVR mock server).
"""
import asyncio
import os
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from shuo.bench import (
    ScenarioConfig,
    SuccessCriteria,
    CriteriaResult,
    ScenarioResult,
    load_scenarios,
    evaluate_criteria,
    BenchISP,
    IVRDriver,
    _extract_say_and_gather,
    print_metrics_report,
    _find_free_port,
    _start_ivr_server,
    _wait_for_ivr_ready,
    run_scenario,
    run_benchmark,
)

# Absolute path to eval/scenarios/example_ivr.yaml — one level above tests/
_SCENARIOS_PATH = str(
    Path(__file__).parent.parent / "eval" / "scenarios" / "example_ivr.yaml"
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


# =============================================================================
# _extract_say_and_gather (BENCH-04 helper)
# =============================================================================

def test_extract_say_and_gather_say_redirect():
    """Parse a <Say> + <Redirect> TwiML (say node)."""
    xml = (
        '<?xml version="1.0"?>'
        '<Response>'
        '<Say>Welcome to the IVR system.</Say>'
        '<Redirect>http://127.0.0.1:9999/ivr/step?node=main_menu</Redirect>'
        '</Response>'
    )
    say, gather_node, redirect_node, has_hangup = _extract_say_and_gather(xml)
    assert say == "Welcome to the IVR system."
    assert gather_node is None
    assert redirect_node == "main_menu"
    assert has_hangup is False


def test_extract_say_and_gather_menu_gather():
    """Parse a <Gather> TwiML (menu node)."""
    xml = (
        '<?xml version="1.0"?>'
        '<Response>'
        '<Gather action="http://127.0.0.1:9999/ivr/gather?node=main_menu" '
        'method="POST" timeout="5" numDigits="1">'
        '<Say>Press 1 for sales.</Say>'
        '</Gather>'
        '<Redirect>http://127.0.0.1:9999/ivr/step?node=main_menu</Redirect>'
        '</Response>'
    )
    say, gather_node, redirect_node, has_hangup = _extract_say_and_gather(xml)
    assert say == "Press 1 for sales."
    assert gather_node == "main_menu"
    assert has_hangup is False


def test_extract_say_and_gather_hangup():
    """Parse a pure <Hangup/> TwiML."""
    xml = '<?xml version="1.0"?><Response><Hangup/></Response>'
    say, gather_node, redirect_node, has_hangup = _extract_say_and_gather(xml)
    assert say == ""
    assert gather_node is None
    assert redirect_node is None
    assert has_hangup is True


# =============================================================================
# BenchISP DTMF capture (BENCH-02)
# =============================================================================

@pytest.mark.asyncio
async def test_bench_isp_captures_dtmf():
    """BenchISP.send_dtmf appends to dtmf_log and enqueues digit."""
    isp = BenchISP()
    await isp.send_dtmf("1")
    await isp.send_dtmf("2")
    assert isp.dtmf_log == ["1", "2"]
    assert isp._dtmf_queue.qsize() == 2


# =============================================================================
# run_scenario wires BenchISP + run_conversation (BENCH-02)
# =============================================================================

@pytest.mark.asyncio
async def test_run_scenario_wires_localISP(tmp_path):
    """run_scenario creates a BenchISP, wires it to run_conversation, returns ScenarioResult."""
    from shuo.bench import run_scenario

    scenario = ScenarioConfig(
        id="test-01",
        description="Test wiring",
        agent={"goal": "Navigate the IVR", "identity": "Customer"},
        success_criteria=SuccessCriteria(transcript_contains=["welcome"]),
        timeout=5,
    )

    async def _fake_run_conversation(isp, **kwargs):
        # Simulate: conversation fires observer with a transcript event then stops
        observer = kwargs.get("observer")
        if observer:
            observer({"type": "transcript", "text": "welcome to the system"})
        # Also simulate _inject being set so IVRDriver can proceed
        await asyncio.sleep(0.05)

    with patch("shuo.call.run_call", side_effect=_fake_run_conversation), \
         patch("shuo.bench.IVRDriver") as MockDriver:
        # Make IVRDriver.drive a coroutine that returns immediately
        driver_instance = MagicMock()
        driver_instance._turn_count = 2
        driver_instance.drive = AsyncMock(return_value=None)
        driver_instance.all_transcripts = ["welcome to the system"]
        MockDriver.return_value = driver_instance

        result = await run_scenario(scenario, "http://127.0.0.1:9999")

    assert isinstance(result, ScenarioResult)
    assert result.scenario_id == "test-01"
    assert "welcome to the system" in result.transcript
    assert result.turns == 2


# =============================================================================
# run_benchmark does not require API keys (BENCH-02)
# =============================================================================

def test_bench_no_api_keys(tmp_path):
    """run_benchmark can be called without DEEPGRAM_API_KEY, GROQ_API_KEY, ELEVENLABS_API_KEY."""
    import os
    from shuo.bench import run_benchmark

    yaml_file = tmp_path / "scenarios.yaml"
    yaml_file.write_text(
        "scenarios:\n"
        "  - id: s1\n"
        "    description: No API keys needed\n"
        "    agent:\n"
        "      goal: Navigate\n"
        "      identity: Customer\n"
        "    success_criteria: {}\n"
    )

    async def _fake_run_benchmark(dataset_path, output_path=None):
        # Import must succeed without env vars
        from shuo.bench import load_scenarios, BenchISP, IVRDriver
        scenarios = load_scenarios(dataset_path)
        assert len(scenarios) == 1
        return []

    with patch("shuo.bench._find_free_port", return_value=19999), \
         patch("shuo.bench._start_ivr_server"), \
         patch("shuo.bench._wait_for_ivr_ready", new_callable=AsyncMock), \
         patch("shuo.bench.run_scenario", new_callable=AsyncMock, return_value=ScenarioResult(
             scenario_id="s1",
             passed=True,
             criteria=CriteriaResult(True, True, True, True),
             turns=1,
             dtmf_log=[],
             transcript=["hello"],
             wall_clock_s=0.5,
         )), \
         patch.dict(os.environ, {}, clear=False):
        # Explicitly unset API keys to confirm no check happens
        for key in ["DEEPGRAM_API_KEY", "GROQ_API_KEY", "ELEVENLABS_API_KEY"]:
            os.environ.pop(key, None)

        results = asyncio.run(run_benchmark(str(yaml_file)))
        assert len(results) == 1
        assert results[0].passed is True


# =============================================================================
# print_metrics_report (BENCH-02)
# =============================================================================

def test_metrics_report_fields():
    """print_metrics_report outputs PASS/FAIL, success rate, and latency."""
    from click.testing import CliRunner
    import click

    results = [
        ScenarioResult(
            scenario_id="scenario-pass",
            passed=True,
            criteria=CriteriaResult(True, True, True, True),
            turns=3,
            dtmf_log=["1"],
            transcript=["Press 1 for sales."],
            wall_clock_s=1.23,
        ),
        ScenarioResult(
            scenario_id="scenario-fail",
            passed=False,
            criteria=CriteriaResult(True, False, True, False),
            turns=2,
            dtmf_log=["9"],
            transcript=["Error"],
            wall_clock_s=0.77,
        ),
    ]

    output_lines = []

    @click.command()
    def _capture():
        print_metrics_report(results)

    runner = CliRunner()
    res = runner.invoke(_capture)
    output = res.output

    assert "PASS" in output
    assert "FAIL" in output
    assert "50%" in output or "50" in output  # success rate
    assert "1.23" in output or "1.2" in output  # latency value


# =============================================================================
# BENCH-05: E2E — sample scenarios load and validate (schema check)
# =============================================================================


def test_sample_scenarios_valid():
    """load_scenarios returns valid ScenarioConfig objects from example_ivr.yaml."""
    scenarios = load_scenarios(_SCENARIOS_PATH)

    assert isinstance(scenarios, list)
    assert len(scenarios) == 8

    ids = [s.id for s in scenarios]
    assert "navigate-to-sales" in ids
    assert "navigate-to-tech-support" in ids
    assert "timeout-no-input" in ids

    for s in scenarios:
        assert isinstance(s, ScenarioConfig)
        assert s.id, "scenario id must be non-empty"
        assert s.description, "scenario description must be non-empty"
        assert "goal" in s.agent, "scenario agent must have a 'goal' key"
        assert isinstance(s.success_criteria, SuccessCriteria)


# =============================================================================
# BENCH-05: E2E — sample scenarios pass against real IVR server
# =============================================================================


@pytest.mark.asyncio
async def test_sample_scenarios_pass():
    """All 3 sample scenarios pass when run against the real IVR mock server.

    run_conversation is mocked to simulate the agent's DTMF responses for each
    scenario, so no real LLM API keys are required.
    """
    from shuo.call import UserSpokeEvent as FluxEndOfTurnEvent

    # ---------------------------------------------------------------------------
    # Per-scenario fake agent factories
    # ---------------------------------------------------------------------------

    def _make_fake_conversation(scenario_id: str):
        """Return an async fake run_conversation for the given scenario."""

        async def _fake(isp, *, observer=None, get_goal=None, **kwargs):
            # Set the _inject hook so IVRDriver can start driving
            event_queue: asyncio.Queue = asyncio.Queue()
            isp._inject = event_queue.put_nowait

            if scenario_id == "navigate-to-sales":
                # Press "1" when the main menu is heard; exit after sales greeting
                dtmf_sent = False
                while True:
                    try:
                        event = await asyncio.wait_for(event_queue.get(), timeout=3.0)
                    except asyncio.TimeoutError:
                        break
                    if observer:
                        observer({"type": "transcript", "text": event.transcript})
                    text_lower = event.transcript.lower()
                    if not dtmf_sent and "press 1 for sales" in text_lower:
                        await isp.send_dtmf("1")
                        dtmf_sent = True
                    elif dtmf_sent and "thank you for your interest" in text_lower:
                        # Reached sales department — conversation done
                        break

            elif scenario_id == "navigate-to-tech-support":
                # Press "2" at main_menu, then "1" at support_menu
                support_pressed = False
                while True:
                    try:
                        event = await asyncio.wait_for(event_queue.get(), timeout=3.0)
                    except asyncio.TimeoutError:
                        break
                    if observer:
                        observer({"type": "transcript", "text": event.transcript})
                    text_lower = event.transcript.lower()
                    if not support_pressed and "press 2 for support" in text_lower:
                        await isp.send_dtmf("2")
                        support_pressed = True
                    elif support_pressed and "press 1 for technical" in text_lower:
                        await isp.send_dtmf("1")
                    elif "please describe your issue" in text_lower:
                        # Reached tech support — conversation done
                        break

            else:
                # timeout-no-input: set _inject and do nothing
                # The scenario will be cancelled by the timeout in run_scenario
                await asyncio.sleep(15.0)

        return _fake

    # ---------------------------------------------------------------------------
    # Start the IVR server once for all 3 scenarios
    # ---------------------------------------------------------------------------
    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    _start_ivr_server(port)
    await _wait_for_ivr_ready(base_url, timeout=10.0)

    scenarios = load_scenarios(_SCENARIOS_PATH)
    scenario_map = {s.id: s for s in scenarios}

    # ---------------------------------------------------------------------------
    # navigate-to-sales
    # ---------------------------------------------------------------------------
    sales_scenario = scenario_map["navigate-to-sales"]
    fake_sales = _make_fake_conversation("navigate-to-sales")
    with patch("shuo.call.run_call", side_effect=fake_sales):
        sales_result = await run_scenario(sales_scenario, base_url)

    assert sales_result.passed is True, (
        f"navigate-to-sales failed: dtmf_log={sales_result.dtmf_log!r}, "
        f"transcript={sales_result.transcript!r}, error={sales_result.error!r}"
    )
    assert sales_result.dtmf_log == ["1"]
    assert any("sales" in line.lower() for line in sales_result.transcript)

    # ---------------------------------------------------------------------------
    # navigate-to-tech-support
    # ---------------------------------------------------------------------------
    tech_scenario = scenario_map["navigate-to-tech-support"]
    fake_tech = _make_fake_conversation("navigate-to-tech-support")
    with patch("shuo.call.run_call", side_effect=fake_tech):
        tech_result = await run_scenario(tech_scenario, base_url)

    assert tech_result.passed is True, (
        f"navigate-to-tech-support failed: dtmf_log={tech_result.dtmf_log!r}, "
        f"transcript={tech_result.transcript!r}, error={tech_result.error!r}"
    )
    assert tech_result.dtmf_log == ["2", "1"]

    # ---------------------------------------------------------------------------
    # timeout-no-input
    # ---------------------------------------------------------------------------
    timeout_scenario = scenario_map["timeout-no-input"]
    fake_timeout = _make_fake_conversation("timeout-no-input")
    with patch("shuo.call.run_call", side_effect=fake_timeout):
        timeout_result = await run_scenario(timeout_scenario, base_url)

    assert timeout_result.passed is True, (
        f"timeout-no-input failed: turns={timeout_result.turns!r}, "
        f"error={timeout_result.error!r}"
    )
    assert timeout_result.turns <= 20


# =============================================================================
# BENCH-05: CLI bench command integration test
# =============================================================================


def test_cli_bench_integration(tmp_path):
    """bench CLI command runs, calls run_benchmark, and prints scenario IDs."""
    from click.testing import CliRunner
    from shuo.cli import bench, cli

    pre_built_results = [
        ScenarioResult(
            scenario_id="navigate-to-sales",
            passed=True,
            criteria=CriteriaResult(True, True, True, True),
            turns=2,
            dtmf_log=["1"],
            transcript=["Press 1 for sales.", "Thank you for your interest."],
            wall_clock_s=0.5,
        ),
        ScenarioResult(
            scenario_id="navigate-to-tech-support",
            passed=True,
            criteria=CriteriaResult(True, True, True, True),
            turns=4,
            dtmf_log=["2", "1"],
            transcript=["Press 2 for support.", "Press 1 for technical support.", "Describe your issue."],
            wall_clock_s=0.8,
        ),
        ScenarioResult(
            scenario_id="timeout-no-input",
            passed=True,
            criteria=CriteriaResult(True, True, True, True),
            turns=2,
            dtmf_log=[],
            transcript=[],
            wall_clock_s=10.0,
        ),
    ]

    async def _fake_run_benchmark(dataset_path, output_path=None):
        print_metrics_report(pre_built_results)
        return pre_built_results

    runner = CliRunner()
    with patch("shuo.bench.run_benchmark", side_effect=_fake_run_benchmark):
        result = runner.invoke(
            cli,
            ["bench", "--dataset", _SCENARIOS_PATH],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, f"CLI exited with {result.exit_code}: {result.output}"
    assert "Benchmark Results" in result.output or "navigate-to-sales" in result.output
    assert "navigate-to-sales" in result.output
    assert "navigate-to-tech-support" in result.output
    assert "timeout-no-input" in result.output
