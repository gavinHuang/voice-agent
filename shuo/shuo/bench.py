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

# ===========================================================================
# Two-Agent Benchmark — Data Model (Task 1.1)
# ===========================================================================

@dataclass
class TwoAgentSuccessCriteria:
    """Criteria for a two-agent scenario."""
    goal_phrases: list[str] = field(default_factory=list)
    verification_phrases: list[str] = field(default_factory=list)
    require_verification_confirmed: bool = False
    max_turns: Optional[int] = None


@dataclass
class TwoAgentScenarioConfig:
    """Configuration for a single two-agent benchmark scenario."""
    id: str
    description: str
    caller: dict                     # {"goal": str, "identity": str, "context": str}
    answerer: dict                   # {"goal": str, "opening_line": str}
    success_criteria: TwoAgentSuccessCriteria
    difficulty: str = "medium"
    timeout: int = 120


@dataclass
class TwoAgentCriteriaResult:
    """Outcome of evaluating two-agent success criteria."""
    goal_phrases_pass: bool
    verification_pass: bool
    turns_pass: bool
    passed: bool


@dataclass
class TwoAgentScenarioResult:
    """Full result record for a single two-agent scenario run."""
    scenario_id: str
    difficulty: str
    passed: bool
    criteria: TwoAgentCriteriaResult
    turns: int
    bilateral_transcript: list[dict]  # [{"role": "caller"|"answerer", "text": str}]
    wall_clock_s: float
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Two-agent scenario loading (Task 1.2)
# ---------------------------------------------------------------------------

def load_two_agent_scenarios(path: str) -> list[TwoAgentScenarioConfig]:
    """Load two-agent scenarios from a YAML file.

    Args:
        path: Path to YAML file with a top-level ``scenarios:`` list.

    Returns:
        List of :class:`TwoAgentScenarioConfig` objects.

    Raises:
        ValueError: If a required field is missing from any scenario.
    """
    with open(path) as fh:
        data = yaml.safe_load(fh)

    scenarios: list[TwoAgentScenarioConfig] = []
    for raw in data["scenarios"]:
        for required in ("id", "description"):
            if required not in raw:
                raise ValueError(
                    f"Scenario missing required field '{required}'. "
                    f"Found fields: {list(raw.keys())}"
                )
        caller = raw.get("caller") or {}
        if not caller.get("goal"):
            raise ValueError(
                f"Scenario '{raw.get('id', '?')}' missing required field 'caller.goal'"
            )
        answerer = raw.get("answerer") or {}
        if not answerer.get("goal"):
            raise ValueError(
                f"Scenario '{raw.get('id', '?')}' missing required field 'answerer.goal'"
            )
        criteria_raw = raw.get("success_criteria") or {}
        criteria = TwoAgentSuccessCriteria(
            goal_phrases=criteria_raw.get("goal_phrases") or [],
            verification_phrases=criteria_raw.get("verification_phrases") or [],
            require_verification_confirmed=criteria_raw.get("require_verification_confirmed", False),
            max_turns=criteria_raw.get("max_turns"),
        )
        scenarios.append(TwoAgentScenarioConfig(
            id=raw["id"],
            description=raw["description"],
            caller=caller,
            answerer=answerer,
            success_criteria=criteria,
            difficulty=raw.get("difficulty", "medium"),
            timeout=raw.get("timeout", 120),
        ))
    return scenarios


# ---------------------------------------------------------------------------
# TwoAgentBridge — cross-injects agent speech (Tasks 2.1, 2.2, 2.3)
# ---------------------------------------------------------------------------

class TwoAgentBridge:
    """Routes each agent's finished-turn text into the peer as FluxEndOfTurnEvent.

    Each agent's observer callback captures transcript events and injects them
    into the peer's ``_inject`` queue entry point. Turn counting and bilateral
    transcript capture happen here.
    """

    def __init__(
        self,
        caller_isp: BenchISP,
        answerer_isp: BenchISP,
        max_turns: int = 50,
        opening_line: str = "",
    ) -> None:
        self._caller_isp = caller_isp
        self._answerer_isp = answerer_isp
        self._max_turns = min(max_turns, 50)
        self._opening_line = opening_line
        self._total_turns: int = 0
        self._bilateral_transcript: list[dict] = []
        self._max_turns_event: asyncio.Event = asyncio.Event()

    @property
    def total_turns(self) -> int:
        return self._total_turns

    @property
    def bilateral_transcript(self) -> list[dict]:
        return self._bilateral_transcript

    def make_caller_observer(self):
        """Return an observer callback for the caller agent.

        Accumulates agent_token events; on agent_done injects the full
        LLM-generated text into the answerer's _inject queue.
        """
        tokens: list[str] = []

        def observer(event: dict) -> None:
            t = event.get("type")
            if t == "agent_token":
                tokens.append(event.get("token", ""))
            elif t == "agent_done":
                text = "".join(tokens).strip()
                tokens.clear()
                if not text:
                    return
                self._bilateral_transcript.append({"role": "caller", "text": text})
                self._total_turns += 1
                if self._total_turns >= self._max_turns:
                    self._max_turns_event.set()
                    return
                if self._answerer_isp._inject is not None:
                    from shuo.types import FluxEndOfTurnEvent
                    self._answerer_isp._inject(FluxEndOfTurnEvent(transcript=text))

        return observer

    def make_answerer_observer(self):
        """Return an observer callback for the answerer agent.

        Accumulates agent_token events; on agent_done injects the full
        LLM-generated text into the caller's _inject queue.
        """
        tokens: list[str] = []

        def observer(event: dict) -> None:
            t = event.get("type")
            if t == "agent_token":
                tokens.append(event.get("token", ""))
            elif t == "agent_done":
                text = "".join(tokens).strip()
                tokens.clear()
                if not text:
                    return
                self._bilateral_transcript.append({"role": "answerer", "text": text})
                self._total_turns += 1
                if self._total_turns >= self._max_turns:
                    self._max_turns_event.set()
                    return
                if self._caller_isp._inject is not None:
                    from shuo.types import FluxEndOfTurnEvent
                    self._caller_isp._inject(FluxEndOfTurnEvent(transcript=text))

        return observer

    async def wait_ready(self) -> bool:
        """Poll until both _inject are set (up to 0.5s). Returns True if ready."""
        for _ in range(50):
            if (self._caller_isp._inject is not None and
                    self._answerer_isp._inject is not None):
                return True
            await asyncio.sleep(0.01)
        return False

    async def fire_initial_event(self) -> None:
        """Inject the first event to start the conversation."""
        from shuo.types import FluxEndOfTurnEvent
        if self._opening_line:
            # Answerer speaks first — inject opening into caller
            self._bilateral_transcript.append({"role": "answerer", "text": self._opening_line})
            self._total_turns += 1
            if self._caller_isp._inject is not None:
                self._caller_isp._inject(FluxEndOfTurnEvent(transcript=self._opening_line))
        else:
            # Caller speaks first via synthetic connection event
            if self._caller_isp._inject is not None:
                self._caller_isp._inject(FluxEndOfTurnEvent(transcript="[call connected]"))

    async def wait_for_max_turns(self) -> None:
        """Await until max_turns is reached."""
        await self._max_turns_event.wait()


# ---------------------------------------------------------------------------
# Two-agent criteria evaluation (Task 3.2)
# ---------------------------------------------------------------------------

def evaluate_two_agent_criteria(
    criteria: TwoAgentSuccessCriteria,
    bilateral_transcript: list[dict],
    turns: int,
) -> TwoAgentCriteriaResult:
    """Evaluate two-agent success criteria against collected run data.

    Args:
        criteria: The :class:`TwoAgentSuccessCriteria` from the scenario config.
        bilateral_transcript: List of ``{"role": ..., "text": ...}`` dicts.
        turns: Total number of agent turns completed.

    Returns:
        A :class:`TwoAgentCriteriaResult` with per-criterion pass/fail and overall.
    """
    # goal_phrases: all must appear in combined transcript (case-insensitive)
    if not criteria.goal_phrases:
        goal_phrases_pass = True
    else:
        full_text = " ".join(e["text"] for e in bilateral_transcript).lower()
        goal_phrases_pass = all(p.lower() in full_text for p in criteria.goal_phrases)

    # verification_pass: any verification_phrase in answerer speech
    if not criteria.require_verification_confirmed or not criteria.verification_phrases:
        verification_pass = True
    else:
        answerer_text = " ".join(
            e["text"] for e in bilateral_transcript if e["role"] == "answerer"
        ).lower()
        verification_pass = any(p.lower() in answerer_text for p in criteria.verification_phrases)

    # turns_pass
    if criteria.max_turns is None:
        turns_pass = True
    else:
        turns_pass = turns <= criteria.max_turns

    return TwoAgentCriteriaResult(
        goal_phrases_pass=goal_phrases_pass,
        verification_pass=verification_pass,
        turns_pass=turns_pass,
        passed=goal_phrases_pass and verification_pass and turns_pass,
    )


# ---------------------------------------------------------------------------
# run_two_agent_scenario — orchestrates one two-agent run (Task 3.1)
# ---------------------------------------------------------------------------

async def run_two_agent_scenario(scenario: TwoAgentScenarioConfig) -> TwoAgentScenarioResult:
    """Run a single two-agent scenario and return its result.

    Creates two paired BenchISP instances (caller + answerer), starts both
    run_conversation tasks, bridges their speech via TwoAgentBridge, and
    evaluates success criteria on completion.
    """
    from shuo.conversation import run_conversation

    caller_isp = BenchISP()
    answerer_isp = BenchISP()
    LocalISP.pair(caller_isp, answerer_isp)

    max_turns = scenario.success_criteria.max_turns or 50
    opening_line = scenario.answerer.get("opening_line") or ""

    bridge = TwoAgentBridge(
        caller_isp=caller_isp,
        answerer_isp=answerer_isp,
        max_turns=max_turns,
        opening_line=opening_line,
    )

    # Build goal strings
    caller_goal = scenario.caller.get("goal", "")
    if scenario.caller.get("identity"):
        caller_goal = f"You are {scenario.caller['identity']}. {caller_goal}"
    if scenario.caller.get("context"):
        caller_goal = f"{caller_goal}\n\nContext: {scenario.caller['context']}"
    answerer_goal = scenario.answerer.get("goal", "")

    start_time = time.monotonic()
    error: Optional[str] = None

    caller_task = asyncio.create_task(
        run_conversation(
            caller_isp,
            observer=bridge.make_caller_observer(),
            get_goal=lambda _: caller_goal,
            tts_pool=_BenchTTSPool(),
            flux_pool=_BenchFluxPool(),
            ivr_mode=lambda: True,
        )
    )
    answerer_task = asyncio.create_task(
        run_conversation(
            answerer_isp,
            observer=bridge.make_answerer_observer(),
            get_goal=lambda _: answerer_goal,
            tts_pool=_BenchTTSPool(),
            flux_pool=_BenchFluxPool(),
            ivr_mode=lambda: True,
        )
    )

    ready = await bridge.wait_ready()
    if not ready:
        error = "Timeout waiting for agents to be ready (>0.5s)"
        for t in [caller_task, answerer_task]:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        criteria = evaluate_two_agent_criteria(
            scenario.success_criteria, bridge.bilateral_transcript, bridge.total_turns
        )
        return TwoAgentScenarioResult(
            scenario_id=scenario.id,
            difficulty=scenario.difficulty,
            passed=False,
            criteria=criteria,
            turns=bridge.total_turns,
            bilateral_transcript=bridge.bilateral_transcript,
            wall_clock_s=time.monotonic() - start_time,
            error=error,
        )

    await bridge.fire_initial_event()

    max_turns_task = asyncio.create_task(bridge.wait_for_max_turns())

    try:
        done, _ = await asyncio.wait(
            [caller_task, answerer_task, max_turns_task],
            timeout=scenario.timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            error = f"timeout after {scenario.timeout}s"
        elif max_turns_task in done and not caller_task.done() and not answerer_task.done():
            error = "max_turns_exceeded"
    except Exception as exc:
        error = str(exc)

    # Cancel all remaining tasks
    for t in [caller_task, answerer_task, max_turns_task]:
        if not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    elapsed = time.monotonic() - start_time
    criteria = evaluate_two_agent_criteria(
        scenario.success_criteria, bridge.bilateral_transcript, bridge.total_turns
    )
    return TwoAgentScenarioResult(
        scenario_id=scenario.id,
        difficulty=scenario.difficulty,
        passed=criteria.passed,
        criteria=criteria,
        turns=bridge.total_turns,
        bilateral_transcript=bridge.bilateral_transcript,
        wall_clock_s=elapsed,
        error=error,
    )


# ---------------------------------------------------------------------------
# Reporting (Tasks 4.1, 4.2, 4.3)
# ---------------------------------------------------------------------------

def print_two_agent_metrics_report(results: list[TwoAgentScenarioResult]) -> None:
    """Print a formatted two-agent metrics table to stdout."""
    if not results:
        click.echo("No results to report.")
        return

    header = f"{'ID':<30} {'Difficulty':<10} {'Result':<8} {'Turns':>6} {'Latency(s)':>11}"
    click.echo(header)
    click.echo("-" * len(header))

    total_turns = 0
    total_latency = 0.0
    pass_count = 0

    for r in results:
        result_str = "PASS" if r.passed else "FAIL"
        click.echo(
            f"{r.scenario_id:<30} {r.difficulty:<10} {result_str:<8} "
            f"{r.turns:>6} {r.wall_clock_s:>10.2f}s"
        )
        total_turns += r.turns
        total_latency += r.wall_clock_s
        if r.passed:
            pass_count += 1

    click.echo("-" * len(header))
    count = len(results)
    click.echo(
        f"Summary: {pass_count}/{count} passed ({pass_count / count * 100:.0f}%), "
        f"avg turns={total_turns / count:.1f}, avg latency={total_latency / count:.2f}s"
    )


def write_run_reports(
    results: list[TwoAgentScenarioResult],
    dataset_path: str,
) -> tuple[str, str]:
    """Write per-run JSON and Markdown report files.

    Creates ``reports/`` if needed and writes
    ``reports/<stem>_<timestamp>.json`` and ``reports/<stem>_<timestamp>.md``.

    Returns:
        (json_path, md_path) as strings.
    """
    import json
    from datetime import datetime
    from pathlib import Path

    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)

    stem = Path(dataset_path).stem
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    json_path = reports_dir / f"{stem}_{ts}.json"
    md_path = reports_dir / f"{stem}_{ts}.md"

    # JSON report
    serialized = [
        {
            "scenario_id": r.scenario_id,
            "difficulty": r.difficulty,
            "passed": r.passed,
            "turns": r.turns,
            "wall_clock_s": r.wall_clock_s,
            "error": r.error,
            "criteria": {
                "goal_phrases_pass": r.criteria.goal_phrases_pass,
                "verification_pass": r.criteria.verification_pass,
                "turns_pass": r.criteria.turns_pass,
                "passed": r.criteria.passed,
            },
            "bilateral_transcript": r.bilateral_transcript,
        }
        for r in results
    ]
    with open(json_path, "w") as fh:
        json.dump(serialized, fh, indent=2)

    # Markdown report
    count = len(results)
    pass_count = sum(1 for r in results if r.passed)
    avg_turns = sum(r.turns for r in results) / count if count else 0
    avg_latency = sum(r.wall_clock_s for r in results) / count if count else 0

    rows = "\n".join(
        f"| {r.scenario_id} | {r.difficulty} | {'PASS' if r.passed else 'FAIL'} "
        f"| {r.turns} | {r.wall_clock_s:.2f}s |"
        for r in results
    )

    diff_lines = []
    for diff in ("easy", "medium", "hard"):
        diff_rs = [r for r in results if r.difficulty == diff]
        if diff_rs:
            dp = sum(1 for r in diff_rs if r.passed)
            diff_lines.append(f"- **{diff.capitalize()} pass rate:** {dp}/{len(diff_rs)}")

    md_content = (
        f"# Benchmark Report: {stem}\n\n"
        f"**Run:** {ts}  \n"
        f"**Dataset:** {dataset_path}  \n"
        f"**Pass rate:** {pass_count}/{count} ({pass_count / count * 100:.0f}%)\n\n"
        "## Results\n\n"
        "| Scenario ID | Difficulty | Result | Turns | Latency |\n"
        "|-------------|-----------|--------|-------|--------|\n"
        f"{rows}\n\n"
        "## Summary\n\n"
        f"- **Total scenarios:** {count}\n"
        f"- **Passed:** {pass_count}\n"
        f"- **Pass rate:** {pass_count / count * 100:.0f}%\n"
        f"- **Avg turns:** {avg_turns:.1f}\n"
        f"- **Avg latency:** {avg_latency:.2f}s\n"
        + ("\n".join(diff_lines) + "\n" if diff_lines else "")
    )
    with open(md_path, "w") as fh:
        fh.write(md_content)

    return str(json_path), str(md_path)


def append_summary(
    results: list[TwoAgentScenarioResult],
    dataset_path: str,
    summary_path: str,
) -> None:
    """Append a run summary block to the shared cumulative Markdown summary file.

    Creates the file (with a header) if it does not yet exist.
    """
    from datetime import datetime
    from pathlib import Path

    Path(summary_path).parent.mkdir(parents=True, exist_ok=True)

    count = len(results)
    pass_count = sum(1 for r in results if r.passed)
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    stem = Path(dataset_path).stem

    lines = [
        f"\n## Run: {ts} — {stem}\n",
        f"- **Dataset:** {dataset_path}",
        f"- **Pass rate:** {pass_count}/{count} ({pass_count / count * 100:.0f}%)",
    ]
    for diff in ("easy", "medium", "hard"):
        diff_rs = [r for r in results if r.difficulty == diff]
        if diff_rs:
            dp = sum(1 for r in diff_rs if r.passed)
            lines.append(f"- **{diff.capitalize()}:** {dp}/{len(diff_rs)}")

    exists = Path(summary_path).exists()
    with open(summary_path, "a") as fh:
        if not exists:
            fh.write("# Benchmark Summary\n")
        fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# run_two_agent_benchmark — sequences all two-agent scenarios (Task 3.3)
# ---------------------------------------------------------------------------

async def run_two_agent_benchmark(
    dataset_path: str,
    summary_path: str = "reports/bench_summary.md",
) -> list[TwoAgentScenarioResult]:
    """Load two-agent scenarios, run each, print results, and write reports.

    Args:
        dataset_path: Path to two-agent YAML scenario file.
        summary_path: Path for the shared cumulative summary Markdown file.

    Returns:
        List of TwoAgentScenarioResult objects.
    """
    scenarios = load_two_agent_scenarios(dataset_path)
    results: list[TwoAgentScenarioResult] = []

    for scenario in scenarios:
        click.echo(f"Running: {scenario.id} [{scenario.difficulty}]...")
        result = await run_two_agent_scenario(scenario)
        status = "PASS" if result.passed else "FAIL"
        click.echo(f"  {status} ({result.turns} turns, {result.wall_clock_s:.1f}s)")
        results.append(result)

    click.echo()
    print_two_agent_metrics_report(results)

    json_path, md_path = write_run_reports(results, dataset_path)
    click.echo(f"\nReports: {json_path}, {md_path}")
    append_summary(results, dataset_path, summary_path)
    click.echo(f"Summary: {summary_path}")

    return results


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
