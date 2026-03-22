"""Benchmark data model and runner.

Exports: SuccessCriteria, ScenarioConfig, CriteriaResult, ScenarioResult,
         load_scenarios, evaluate_criteria,
         IVRDriver, BenchISP, run_scenario, run_benchmark, print_metrics_report
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, urlparse

import click
import httpx
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


# ---------------------------------------------------------------------------
# BenchISP — LocalISP subclass for DTMF capture (BENCH-02)
# ---------------------------------------------------------------------------

from shuo.services.local_isp import LocalISP  # noqa: E402


class BenchISP(LocalISP):
    """LocalISP subclass that records DTMF digits and queues them for IVRDriver."""

    def __init__(self) -> None:
        super().__init__()
        self.dtmf_log: list[str] = []
        self._dtmf_queue: asyncio.Queue = asyncio.Queue()

    async def send_dtmf(self, digits: str) -> None:
        """Capture DTMF digits into log and enqueue for IVRDriver.

        Splits multi-digit strings so each digit is presented separately to
        the IVR gather step (which only accepts one digit at a time).
        """
        for d in digits:
            self.dtmf_log.append(d)
            await self._dtmf_queue.put(d)


# ---------------------------------------------------------------------------
# TwiML parser helper
# ---------------------------------------------------------------------------

def _extract_say_and_gather(
    xml_str: str,
) -> tuple[str, Optional[str], Optional[str], bool]:
    """Parse TwiML XML and extract key elements.

    Returns:
        (say_text, gather_node_id, redirect_node_id, has_hangup)
    """
    root = ET.fromstring(xml_str)

    say_text = ""
    say_el = root.find(".//Say")
    if say_el is not None and say_el.text:
        say_text = say_el.text

    gather_node_id: Optional[str] = None
    gather_el = root.find(".//Gather")
    if gather_el is not None:
        action = gather_el.get("action", "")
        parsed = urlparse(action)
        qs = parse_qs(parsed.query)
        node_ids = qs.get("node", [])
        if node_ids:
            gather_node_id = node_ids[0]

    redirect_node_id: Optional[str] = None
    # Only top-level <Redirect> (not inside <Gather>) counts as a step redirect
    redirect_el = root.find("Redirect")
    if redirect_el is not None and redirect_el.text:
        parsed = urlparse(redirect_el.text.strip())
        qs = parse_qs(parsed.query)
        node_ids = qs.get("node", [])
        if node_ids:
            redirect_node_id = node_ids[0]

    has_hangup = root.find(".//Hangup") is not None

    return say_text, gather_node_id, redirect_node_id, has_hangup


# ---------------------------------------------------------------------------
# IVRDriver — walks TwiML state machine via HTTP loopback (BENCH-04)
# ---------------------------------------------------------------------------

class IVRDriver:
    """Drives the IVR server state machine on behalf of a benchmarked agent."""

    def __init__(self, base_url: str, agent_isp: BenchISP) -> None:
        self._base = base_url.rstrip("/")
        self._agent_isp = agent_isp
        self._turn_count = 0
        self.all_transcripts: list[str] = []  # All IVR messages injected (incl. post-hangup)

    async def drive(self, client: httpx.AsyncClient, timeout: float) -> None:
        """Walk the TwiML state machine until a <Hangup/> or timeout."""
        from shuo.types import FluxEndOfTurnEvent

        # Step 1: get the entry TwiML
        resp = await client.post(f"{self._base}/twiml")
        resp.raise_for_status()
        xml_str = resp.text

        per_step_timeout = max(timeout / 2, 5.0)

        # Follow redirects until we reach a Say/Gather or Hangup
        _redirect_limit = 20
        _redirect_count = 0

        while _redirect_count < _redirect_limit:
            say_text, gather_node, redirect_node, has_hangup = _extract_say_and_gather(xml_str)

            if has_hangup and not say_text and not gather_node and not redirect_node:
                # Pure hangup node — done
                break

            if say_text:
                self.all_transcripts.append(say_text)
                if self._agent_isp._inject is not None:
                    self._agent_isp._inject(FluxEndOfTurnEvent(transcript=say_text))
                self._turn_count += 1

            if gather_node is not None:
                # Wait for agent to send a DTMF digit
                digit = await asyncio.wait_for(
                    self._agent_isp._dtmf_queue.get(),
                    timeout=per_step_timeout,
                )
                resp = await client.post(
                    f"{self._base}/ivr/gather",
                    params={"node": gather_node},
                    data={"Digits": digit},
                )
                resp.raise_for_status()
                xml_str = resp.text
                _redirect_count += 1
                continue

            if redirect_node is not None:
                resp = await client.post(
                    f"{self._base}/ivr/step",
                    params={"node": redirect_node},
                )
                resp.raise_for_status()
                xml_str = resp.text
                _redirect_count += 1
                continue

            # No gather, no redirect, no hangup — treated as terminal
            break


# ---------------------------------------------------------------------------
# No-op Flux/TTS pools (benchmark needs no real API keys)
# ---------------------------------------------------------------------------

class _BenchFluxPool:
    """No-op FluxPool: IVR driver injects FluxEndOfTurnEvent directly."""

    async def get(self, on_end_of_turn, on_start_of_turn, **_):
        return self

    async def stop(self) -> None:
        pass

    async def send(self, _) -> None:
        pass


class _BenchTTSPool:
    """No-op TTSPool: no audio synthesis in benchmark mode."""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def get(self, on_audio, on_done):
        from unittest.mock import AsyncMock, MagicMock
        tts = AsyncMock()
        tts.bind = MagicMock()
        # Call on_done when flush() is invoked so AudioPlayer.mark_tts_done() fires
        # and the agent turn completes. Without this the player loop never starts
        # (no audio chunks), mark_tts_done is never called, and the agent hangs
        # in RESPONDING state blocking all subsequent IVR turns.
        async def _flush():
            asyncio.create_task(on_done())
        tts.flush = _flush
        return tts

    @property
    def available(self) -> int:
        return 1


# ---------------------------------------------------------------------------
# run_scenario — orchestrates a single agent + IVR pair (BENCH-02)
# ---------------------------------------------------------------------------

async def run_scenario(scenario: ScenarioConfig, ivr_base_url: str) -> ScenarioResult:
    """Run a single benchmark scenario and return its result.

    Spawns a BenchISP-connected agent and an IVRDriver, lets them interact,
    then evaluates success criteria.
    """
    from shuo.conversation import run_conversation

    bench_isp = BenchISP()

    transcript: list[str] = []

    def observer(event: dict) -> None:
        if event.get("type") == "transcript":
            transcript.append(event["text"])

    if scenario.agent.get("identity"):
        goal = f"You are {scenario.agent['identity']}. {scenario.agent['goal']}"
    else:
        goal = scenario.agent.get("goal", "")

    ivr_driver = IVRDriver(ivr_base_url, bench_isp)

    start_time = time.monotonic()

    agent_task = asyncio.create_task(
        run_conversation(
            bench_isp,
            observer=observer,
            get_goal=lambda _: goal,
            tts_pool=_BenchTTSPool(),
            flux_pool=_BenchFluxPool(),
            ivr_mode=lambda: True,
        )
    )

    # Wait for _inject to be set by run_conversation before driving IVR
    for _ in range(50):  # up to 0.5s
        if bench_isp._inject is not None:
            break
        await asyncio.sleep(0.01)

    async with httpx.AsyncClient() as client:
        ivr_task = asyncio.create_task(
            ivr_driver.drive(client, scenario.timeout)
        )

        # Wait for IVR to complete (reaches hangup node or times out).
        # The agent may hang up before the IVR is done — that's fine, since
        # BenchISP._dtmf_queue holds pre-queued digits the IVR can drain.
        error: Optional[str] = None
        try:
            await asyncio.wait_for(ivr_task, timeout=scenario.timeout)
        except asyncio.TimeoutError:
            error = f"IVR timed out after {scenario.timeout}s"
        except Exception as e:
            error = str(e)

        # Cancel agent if still running (e.g. if IVR completed before agent hangs up)
        if not agent_task.done():
            agent_task.cancel()
            try:
                await agent_task
            except (asyncio.CancelledError, Exception):
                pass

    elapsed = time.monotonic() - start_time

    # Use IVRDriver's full transcript (includes messages injected after agent hangup)
    full_transcript = ivr_driver.all_transcripts

    criteria = evaluate_criteria(
        scenario.success_criteria,
        full_transcript,
        bench_isp.dtmf_log,
        ivr_driver._turn_count,
    )

    return ScenarioResult(
        scenario_id=scenario.id,
        passed=criteria.passed,
        criteria=criteria,
        turns=ivr_driver._turn_count,
        dtmf_log=bench_isp.dtmf_log,
        transcript=full_transcript,
        wall_clock_s=elapsed,
        error=error,
    )


# ---------------------------------------------------------------------------
# IVR server lifecycle helpers
# ---------------------------------------------------------------------------

def _find_free_port() -> int:
    """Return an available ephemeral TCP port on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def _start_ivr_server(port: int, flow_path: Optional[str] = None) -> None:
    """Start the IVR FastAPI app in a daemon thread via uvicorn."""
    import uvicorn

    if flow_path is not None:
        import os
        os.environ["IVR_CONFIG"] = flow_path

    # Deferred import keeps top-level imports lightweight (Phase 3 convention)
    import ivr.server as ivr_server_mod

    config = uvicorn.Config(
        ivr_server_mod.app,
        host="127.0.0.1",
        port=port,
        log_level="error",
    )
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()


async def _wait_for_ivr_ready(base_url: str, timeout: float = 10.0) -> None:
    """Poll GET /health until 200 or timeout."""
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                resp = await client.get(f"{base_url}/health")
                if resp.status_code == 200:
                    return
            except Exception:
                pass
            await asyncio.sleep(0.05)
    raise TimeoutError(f"IVR server at {base_url} did not become ready within {timeout}s")


# ---------------------------------------------------------------------------
# run_benchmark — sequences all scenarios and produces the metrics report
# ---------------------------------------------------------------------------

async def run_benchmark(
    dataset_path: str,
    output_path: Optional[str] = None,
) -> list[ScenarioResult]:
    """Load scenarios, start IVR server, run all scenarios, print report.

    Args:
        dataset_path: Path to YAML scenario file.
        output_path: Optional path to write JSON results.

    Returns:
        List of ScenarioResult objects.
    """
    import json

    scenarios = load_scenarios(dataset_path)

    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    # Determine flow path from first scenario (all scenarios share the server for now)
    flow_path = scenarios[0].ivr_flow if scenarios else None
    _start_ivr_server(port, flow_path)
    await _wait_for_ivr_ready(base_url)

    results: list[ScenarioResult] = []
    for scenario in scenarios:
        result = await run_scenario(scenario, base_url)
        results.append(result)

    print_metrics_report(results)

    if output_path:
        serialized = []
        for r in results:
            serialized.append({
                "scenario_id": r.scenario_id,
                "passed": r.passed,
                "turns": r.turns,
                "dtmf_log": r.dtmf_log,
                "transcript": r.transcript,
                "wall_clock_s": r.wall_clock_s,
                "error": r.error,
                "criteria": {
                    "transcript_pass": r.criteria.transcript_pass,
                    "dtmf_pass": r.criteria.dtmf_pass,
                    "turns_pass": r.criteria.turns_pass,
                    "passed": r.criteria.passed,
                },
            })
        with open(output_path, "w") as fh:
            json.dump(serialized, fh, indent=2)

    return results


# ---------------------------------------------------------------------------
# print_metrics_report — terminal table of results
# ---------------------------------------------------------------------------

def print_metrics_report(results: list[ScenarioResult]) -> None:
    """Print a formatted metrics table to stdout via click.echo."""
    if not results:
        click.echo("No results to report.")
        return

    header = f"{'ID':<30} {'Result':<8} {'Turns':>6} {'DTMF':>8} {'Latency(s)':>11}"
    click.echo(header)
    click.echo("-" * len(header))

    total_turns = 0
    total_latency = 0.0
    pass_count = 0

    for r in results:
        result_str = "PASS" if r.passed else "FAIL"

        # DTMF: show actual digits pressed vs expected
        dtmf_actual = "".join(r.dtmf_log) if r.dtmf_log else "-"
        dtmf_ok = "✓" if r.criteria.dtmf_pass else "✗"

        click.echo(
            f"{r.scenario_id:<30} {result_str:<8} {r.turns:>6} "
            f"{dtmf_actual:>6}{dtmf_ok:>2} {r.wall_clock_s:>10.2f}s"
        )

        total_turns += r.turns
        total_latency += r.wall_clock_s
        if r.passed:
            pass_count += 1

    click.echo("-" * len(header))
    count = len(results)
    success_rate = pass_count / count * 100
    avg_turns = total_turns / count
    avg_latency = total_latency / count
    click.echo(
        f"Summary: {pass_count}/{count} passed ({success_rate:.0f}%), "
        f"avg turns={avg_turns:.1f}, avg latency={avg_latency:.2f}s"
    )
