# Phase 4: IVR Benchmark - Research

**Researched:** 2026-03-21
**Domain:** Benchmark runner, IVR HTTP loopback, TwiML text injection, YAML scenario schema, metrics collection
**Confidence:** HIGH

## Summary

Phase 4 is entirely a wiring and composition task — no new third-party libraries are required. All the building blocks exist in the repository: `ivr/server.py` (FastAPI IVR mock), `ivr/engine.py` (TwiML renderer), `shuo/shuo/services/local_isp.py` (agent ↔ IVR audio bus), and `shuo/shuo/conversation.py` (agent loop). The benchmark runner creates an HTTP loopback pair: the IVR server starts on an ephemeral port via uvicorn in a daemon thread, an `IVRDriver` object talks to it via `httpx.AsyncClient`, and the agent side runs via `run_conversation()` with `ivr_mode=True`.

The key design challenge is the impedance between the IVR's synchronous request/response HTTP model and the agent's async event-driven loop. The IVR driver must walk the TwiML state machine autonomously — parse `<Say>` text from responses, inject that text into the agent's transcript as a synthetic `FluxEndOfTurnEvent`, wait for the agent to respond (observing DTMF events via `LocalISP._inject`), then POST the DTMF digit to `/ivr/gather`. Text injection bypasses all real TTS/STT infrastructure, making scenarios run in milliseconds.

Success criteria evaluation is straightforward post-run: collect the agent-side transcript from observer events, collect DTMF digits from `send_dtmf` intercepts, count turns from `FluxEndOfTurnEvent` firings, and check all three criteria atomically at conversation end.

**Primary recommendation:** Implement a single `IVRDriver` class that owns the HTTP loopback state machine, one `run_scenario()` coroutine that wires up LocalISP + IVRDriver + run_conversation(), and a `run_benchmark()` function that sequences scenarios and builds the metrics report. Place everything in `shuo/shuo/bench.py`.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Scenario YAML schema**
- Agent config is inline per-scenario — each scenario has its own `agent: {goal, identity}` block; scenarios are self-contained
- ALL success criteria must pass for a scenario to be marked passing — no `pass_when: any` mode
- Optional `ivr_flow` field — if absent, defaults to `ivr/flows/example.yaml`; allows future flows without schema changes
- Schema shape:
  ```yaml
  scenarios:
    - id: "navigate-to-sales"
      description: "Agent navigates to sales department via DTMF"
      agent:
        goal: "Navigate the IVR to reach the sales department"
        identity: "Customer"
      ivr_flow: ivr/flows/example.yaml   # optional; defaults to example.yaml
      timeout: 30
      success_criteria:
        transcript_contains:
          - "sales"
        dtmf_sequence: "1"
        max_turns: 5
  ```

**IVR coupling mode**
- HTTP loopback — the IVR FastAPI app (`ivr/server.py`) starts on a local port for each benchmark run; the IVR driver makes HTTP requests to it (`/twiml`, `/ivr/step`, `/ivr/gather`)
- Text injection — bypass TTS — the IVR driver extracts `<Say>` text from TwiML responses and injects it directly into the agent's transcript via the conversation observer/event system; no TTS API calls needed
- DTMF routing via LocalISP — the IVR driver monitors `LocalISP._inject` callback to receive DTMF events from the agent, then makes the corresponding HTTP POST to `/ivr/gather?node=ID`; LocalISP remains the communication bus

### Claude's Discretion
- Where the benchmark runner module lives (`shuo/shuo/bench.py` or `shuo/shuo/services/bench.py` — Claude decides)
- Metrics report format (terminal table per BENCH-04; optional JSON file output if `--output` flag provided)
- Which 3 sample scenarios to include (should cover: happy-path DTMF navigation, multi-step menu traversal, and a timeout/failure case)
- Port selection for the IVR loopback server (ephemeral/random to avoid conflicts)
- Whether scenarios run sequentially or in parallel within a single `bench` invocation
- Exact IVR driver implementation (how TwiML XML is parsed, how state machine tracks current node)

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| BENCH-01 | Benchmark scenario YAML schema defined (id, description, agent configs, success criteria, timeout) | YAML shape locked in CONTEXT.md; `yaml.safe_load()` pattern already used in CLI; dataclass validation mirrors `ivr/config.py` parse pattern |
| BENCH-02 | Benchmark runner spawns agent ↔ IVR pairs using LocalISP | `LocalISP.pair()` + `run_conversation()` pattern is established in `_run_local_call()`; IVR server starts in daemon thread like `shuo/shuo/server.py` start_server pattern |
| BENCH-03 | Success criteria support: `transcript_contains`, `dtmf_sequence`, `max_turns` | Observer callback in `run_conversation()` already surfaces transcript events; DTMF digits available via `LocalISP.send_dtmf` intercept; turn count from `FluxEndOfTurnEvent` count |
| BENCH-04 | Runner outputs metrics: success rate, average turns, DTMF accuracy, wall-clock latency | All metrics derivable from post-run state; `time.monotonic()` for wall-clock; terminal table via standard string formatting or `click.echo` |
| BENCH-05 | At least 3 sample scenarios provided covering the example IVR flow | `ivr/flows/example.yaml` has welcome → main_menu → sales/support/operator paths; 3 scenarios cover happy-path (press 1 → sales), multi-step (press 2 → support_menu → press 1), and failure/timeout |
</phase_requirements>

---

## Standard Stack

### Core (no new dependencies needed)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `httpx` | 0.28.1 (installed) | HTTP client for IVR loopback calls | Already in venv; `AsyncClient` for async HTTP POST to IVR endpoints |
| `uvicorn` | >=0.27.0 (already dep) | Start IVR FastAPI app on loopback port | Same pattern as `shuo/shuo/server.py` daemon-thread startup |
| `pyyaml` | >=6.0.0 (already dep) | Parse scenario YAML files | Already used in CLI config loading; `yaml.safe_load()` |
| `click` | >=8.0.0 (already dep) | Terminal output for metrics report | Already the CLI framework; `click.echo` for tabular output |
| `xml.etree.ElementTree` | stdlib | Parse TwiML XML from IVR responses | Stdlib; already used in `ivr/tests/test_ivr.py` |
| `asyncio` | stdlib | Async orchestration, timeout enforcement | Already drives all conversation loops |

**No new packages to install.** Everything needed is already in `shuo/requirements.txt` or stdlib.

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `time.monotonic()` | stdlib | Wall-clock latency measurement | Wrap each scenario's `run_scenario()` call |
| `threading.Thread` | stdlib | Daemon thread for uvicorn IVR server | Same pattern as `serve` command in `cli.py` |
| `socket` / `socketserver` | stdlib | Find ephemeral free port | `sock = socket.socket(); sock.bind(('', 0)); port = sock.getsockname()[1]; sock.close()` |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| httpx.AsyncClient | aiohttp | httpx is already installed; no reason to add aiohttp |
| xml.etree.ElementTree | lxml | stdlib is sufficient for simple TwiML `<Say>` extraction; no xpath needed |
| Sequential scenario runs | asyncio.gather (parallel) | Sequential is simpler to debug, avoids port conflicts; parallel adds complexity for minimal gain in a benchmark context |

---

## Architecture Patterns

### Recommended Project Structure

```
shuo/shuo/
└── bench.py          # All benchmark code: IVRDriver, ScenarioResult, run_scenario(), run_benchmark()

shuo/tests/
└── test_bench.py     # Benchmark runner tests using MockISP + httpx.AsyncClient ASGI transport

ivr/
└── flows/
    └── example.yaml  # Already exists — used by all 3 sample scenarios

scenarios/
└── example_ivr.yaml  # 3 sample scenarios (new file, project root or shuo/ root)
```

**Decision:** Place runner in `shuo/shuo/bench.py` (flat, not in `services/`) — it is a CLI feature, not a reusable service.

### Pattern 1: IVR Loopback Server Startup

The IVR `FastAPI` app starts in a daemon thread. Port selection uses an ephemeral socket bind to avoid conflicts. The server is started once per benchmark run (not per scenario) for efficiency.

```python
# Source: modeled on shuo/shuo/server.py start_server() pattern + cli.py serve command
import socket
import threading
import uvicorn
from ivr.server import app as ivr_app

def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def _start_ivr_server(port: int) -> None:
    config = uvicorn.Config(ivr_app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    server.run()

port = _find_free_port()
thread = threading.Thread(target=_start_ivr_server, args=(port,), daemon=True)
thread.start()
# Brief sleep or health-check poll before first request
```

**Pitfall:** The IVR server uses module-level `_engine` state. If the benchmark needs to vary `ivr_flow` per scenario, the server must be restarted or `reload_config()` called between scenarios. For Phase 4 (all scenarios use the same `example.yaml` default), a single server instance is fine. For scenarios with custom `ivr_flow`, call `ivr.server.reload_config(yaml_str)` before running the scenario — this function already exists for test use.

### Pattern 2: IVR Driver State Machine

The `IVRDriver` walks the TwiML graph on behalf of the IVR side. It is the "IVR side" of the LocalISP pair. It:
1. POSTs `/twiml` to get the entry redirect
2. Follows redirects to `/ivr/step?node=ID` — extracts `<Say>` text and injects into agent transcript
3. Waits for agent DTMF (via asyncio.Event set in `_inject` override)
4. POSTs `/ivr/gather?node=ID` with the DTMF digit

```python
# Source: derived from ivr/engine.py URL patterns and ivr/server.py endpoint contracts
import httpx
from xml.etree import ElementTree as ET

class IVRDriver:
    def __init__(self, base_url: str, inject_transcript: callable, on_dtmf_received: asyncio.Event):
        self._base = base_url
        self._inject = inject_transcript   # callable(text: str) — pushes FluxEndOfTurnEvent
        self._dtmf_event = on_dtmf_received
        self._last_dtmf: str = ""
        self._current_node: str = ""
        self._dtmf_log: list[str] = []

    async def drive(self, client: httpx.AsyncClient, timeout: float) -> None:
        """Walk IVR state machine until hangup or timeout."""
        # POST /twiml → follow Redirect to first node
        # Loop: render node → inject <Say> text → wait for DTMF → POST /ivr/gather
        ...
```

**Key insight:** The IVR side does NOT need its own ISP instance. It only needs to (a) inject text into the agent's event queue and (b) receive DTMF back from the agent. The `IVRDriver` replaces what would otherwise be a second conversation loop.

### Pattern 3: Text Injection into Agent Transcript

The agent's `run_conversation()` loop receives transcript via `FluxEndOfTurnEvent`. For benchmarking, the IVR driver bypasses real STT by directly pushing this event into the conversation's `event_queue`. However, `event_queue` is private to `run_conversation()`.

**Solution:** Use the existing `observer` callback pattern. The `run_conversation()` function exposes an `observer` param. However, observer is read-only (it receives events, it doesn't inject them).

**Correct injection path:** The `ivr_mode` parameter and the existing `FluxPool`/`FluxService` architecture are not the right channel. The correct approach is:

`run_conversation()` sets `isp._inject = event_queue.put_nowait` when the ISP has `_inject`. This means: if the IVR driver also holds a reference to the agent-side ISP, it can inject synthetic `FluxEndOfTurnEvent` objects directly via `isp._inject`.

```python
# In run_conversation (existing behavior, line 131-132 of conversation.py):
if hasattr(isp, '_inject'):
    isp._inject = event_queue.put_nowait

# IVR driver calls:
from shuo.types import FluxEndOfTurnEvent
agent_isp._inject(FluxEndOfTurnEvent(transcript=say_text))
```

This is the cleanest injection path — it reuses the existing DTMF injection hook as a general event injection channel. No modifications to `run_conversation()` are needed.

### Pattern 4: DTMF Collection from Agent

When the agent responds with a DTMF digit, `run_conversation()` calls `await isp.send_dtmf(event.digits)`. Since the agent-side ISP is a `LocalISP`, `send_dtmf` calls `self._peer._inject(DTMFToneEvent(digits=digit))`.

For the benchmark, there is no peer ISP. Instead, override `send_dtmf` on the agent-side LocalISP (or use a thin wrapper) to capture the digit and signal the IVR driver:

```python
# Source: local_isp.py send_dtmf contract
class BenchISP(LocalISP):
    """LocalISP subclass that routes DTMF to the IVR driver instead of a peer."""

    def __init__(self):
        super().__init__()
        self.dtmf_log: list[str] = []
        self._dtmf_queue: asyncio.Queue = asyncio.Queue()

    async def send_dtmf(self, digit: str) -> None:
        self.dtmf_log.append(digit)
        await self._dtmf_queue.put(digit)  # IVR driver awaits this
```

Alternatively, just override `_inject` on the peer side. Either approach is simple; the subclass approach is more explicit.

### Pattern 5: Scenario Timeout Enforcement

Use `asyncio.wait_for()` wrapping `run_scenario()`, or `asyncio.wait([agent_task, ivr_driver_task], FIRST_COMPLETED)` — the same pattern as `_run_local_call()` in `cli.py`:

```python
# Source: shuo/shuo/cli.py _run_local_call() lines 257-264
done, pending = await asyncio.wait(
    [agent_task, ivr_task],
    return_when=asyncio.FIRST_COMPLETED,
    timeout=scenario.timeout,
)
for t in pending:
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass
```

### Pattern 6: Success Criteria Evaluation

All three criteria are evaluated after the scenario completes, on the collected data:

```python
# transcript_contains: all strings must appear (case-insensitive substring match)
transcript_text = " ".join(transcript_lines).lower()
transcript_ok = all(s.lower() in transcript_text for s in criteria.transcript_contains)

# dtmf_sequence: collected digits joined == expected string
dtmf_ok = "".join(dtmf_log) == criteria.dtmf_sequence  # exact match

# max_turns: turn count <= limit
turns_ok = turn_count <= criteria.max_turns

passed = transcript_ok and dtmf_ok and turns_ok  # ALL must pass
```

**Note on `dtmf_sequence`:** The locked decision says "ALL criteria must pass." If a scenario omits one criterion field (e.g., no `dtmf_sequence`), that criterion is vacuously true (None means skip). This is the standard optional-field pattern.

### Anti-Patterns to Avoid

- **Starting a new uvicorn server per scenario:** Slow and port-leaky. Start once, use `reload_config()` for flow changes.
- **Using real TTS/STT in benchmark:** Defeats the purpose. The `ivr_mode=True` flag in `run_conversation()` suppresses the agent's opening message. The IVR driver must inject text directly, not route audio through Deepgram.
- **Importing `ivr.server.app` at module load time in `bench.py`:** The IVR server uses global `_engine` state. Import inside the function that starts the server to avoid state pollution between test runs.
- **Relying on sleep for server readiness:** Poll `/health` with a short timeout instead of a fixed sleep.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| XML parsing | Custom regex for `<Say>` text | `xml.etree.ElementTree` | TwiML can have escaped chars (`&amp;`, `&lt;`); ET handles this correctly |
| HTTP client for IVR | `urllib` or raw sockets | `httpx.AsyncClient` | Already installed; async context manager; handles form encoding for `/ivr/gather` |
| Port allocation | Hardcoded ports or increment loop | `socket.bind(('', 0))` | OS guarantees no conflict; no retry logic needed |
| TwiML redirect following | Manual URL parsing | Follow the `<Redirect>` URL pattern consistently | `TwiMLEngine._step_url()` always generates `/ivr/step?node=ID` format — parse node ID from query string |
| Metrics formatting | Rich/tabulate library | `click.echo` with f-string table | No new dep; sufficient for a single-table report |

---

## Common Pitfalls

### Pitfall 1: IVR Server Global State Between Scenarios

**What goes wrong:** `ivr/server.py` uses module-level `_engine` singleton. If scenario A uses `example.yaml` and scenario B uses a different flow, calling `reload_config()` for B changes the engine for all concurrent requests.

**Why it happens:** FastAPI app is a module-level singleton; engine is initialized lazily on first request.

**How to avoid:** Run scenarios sequentially (Claude's Discretion — recommended). If different `ivr_flow` values appear across scenarios, call `reload_config()` before each scenario. For Phase 4 all 3 sample scenarios use the same `example.yaml`, so this is not an issue in practice.

**Warning signs:** Scenario B gets wrong TwiML responses if run after a different-flow scenario.

### Pitfall 2: Deadlock Between Agent Loop and IVR Driver

**What goes wrong:** The agent loop waits for `FluxEndOfTurnEvent` (injected by IVR driver). The IVR driver waits for DTMF from the agent. If injection timing is off, both sides block indefinitely.

**Why it happens:** `FluxEndOfTurnEvent` injection via `isp._inject` calls `event_queue.put_nowait` — this is synchronous and always succeeds (queue is unbounded). DTMF collection via the `dtmf_queue` in `BenchISP` also uses an asyncio queue. The IVR driver must `await dtmf_queue.get()` with a timeout, not indefinitely.

**How to avoid:** Always wrap `dtmf_queue.get()` in `asyncio.wait_for(..., timeout=per_step_timeout)`. Use the scenario's `timeout` field divided by expected turns as a per-step timeout.

**Warning signs:** Scenario hangs exactly at scenario timeout rather than failing fast.

### Pitfall 3: run_conversation() Opens Real TTS/STT Connections

**What goes wrong:** `run_conversation()` creates `FluxService` (Deepgram WebSocket) and `TTSPool` (ElevenLabs) if not provided externally. These require live API keys and introduce network latency, breaking the "no Twilio credentials" guarantee.

**Why it happens:** The default code path in `run_conversation()` creates pools internally when `flux_pool` and `tts_pool` are `None`.

**How to avoid:** Pass mock/no-op `flux_pool` and `tts_pool` parameters to `run_conversation()` in the benchmark. The `test_ivr_barge_in.py` test already shows the `MockFluxPool` + `MockTTSPool` pattern. Benchmark scenarios reuse this pattern.

**Warning signs:** `ModuleNotFoundError` for deepgram or `DEEPGRAM_API_KEY` env var errors when running `bench`.

### Pitfall 4: `<Say>` Text Extraction Missing `node` ID

**What goes wrong:** The IVR driver needs to know the current `node` ID to POST to `/ivr/gather?node=ID`. The TwiML response for a menu includes this in the `<Gather action="...">` URL attribute.

**Why it happens:** `TwiMLEngine._gather_url()` embeds the node ID in the action URL: `/ivr/gather?node={node_id}`. The driver must parse this URL, not hardcode node names.

**How to avoid:** Extract the `action` attribute of the `<Gather>` element and parse `node` from its query string using `urllib.parse.urlparse` + `parse_qs`.

**Warning signs:** IVR driver always posts to wrong node ID, causing routing failures.

### Pitfall 5: Observer Transcript Collection Race

**What goes wrong:** Transcript lines from both the agent token stream and the IVR injection are interleaved in the observer. The `agent_token` event type fires per-token; `transcript` events fire per-turn.

**Why it happens:** `run_conversation()` emits `{"type": "transcript", "speaker": "callee", "text": ...}` for STT turns and `{"type": "agent_token", "token": ...}` for agent output tokens.

**How to avoid:** Collect only `type == "transcript"` events for `transcript_contains` evaluation. Collect agent tokens separately if needed for full transcript. For Phase 4, only the injected IVR text (speaker: "callee") and the agent's final output matter for `transcript_contains`.

---

## Code Examples

Verified patterns from existing codebase:

### YAML Scenario Loading
```python
# Source: shuo/shuo/cli.py _load_config() + ivr/config.py parse_config() patterns
import yaml
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class SuccessCriteria:
    transcript_contains: list[str] = field(default_factory=list)
    dtmf_sequence: Optional[str] = None
    max_turns: Optional[int] = None

@dataclass
class ScenarioConfig:
    id: str
    description: str
    agent: dict           # {goal: str, identity: str}
    success_criteria: SuccessCriteria
    timeout: int = 30
    ivr_flow: Optional[str] = None  # defaults to ivr/flows/example.yaml

def load_scenarios(path: str) -> list[ScenarioConfig]:
    with open(path) as f:
        data = yaml.safe_load(f)
    return [_parse_scenario(s) for s in data["scenarios"]]
```

### Free Port Selection
```python
# Source: standard Python socket pattern; no external reference needed
import socket

def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]
```

### TwiML Say Text Extraction
```python
# Source: derived from ivr/engine.py _render_say / _render_menu patterns
from xml.etree import ElementTree as ET
from urllib.parse import urlparse, parse_qs

def _extract_say_and_node(xml_str: str) -> tuple[str, Optional[str]]:
    """Return (say_text, node_id_for_gather) from a TwiML response."""
    root = ET.fromstring(xml_str)
    say_text = ""
    node_id = None

    # <Say> anywhere in the tree
    say_el = root.find(".//Say")
    if say_el is not None:
        say_text = say_el.text or ""

    # <Gather action="...?node=ID"> → extract node ID
    gather_el = root.find(".//Gather")
    if gather_el is not None:
        action = gather_el.get("action", "")
        qs = parse_qs(urlparse(action).query)
        node_id = qs.get("node", [None])[0]

    # <Redirect> → extract node from step URL
    redirect_el = root.find(".//Redirect")
    if redirect_el is not None and redirect_el.text:
        qs = parse_qs(urlparse(redirect_el.text).query)
        node_id = qs.get("node", [node_id])[0]

    return say_text, node_id
```

### Injecting FluxEndOfTurnEvent Into Agent Loop
```python
# Source: shuo/shuo/conversation.py lines 131-132 (existing _inject pattern)
# In run_conversation():
#   if hasattr(isp, '_inject'):
#       isp._inject = event_queue.put_nowait

# IVR driver usage:
from shuo.types import FluxEndOfTurnEvent

def inject_ivr_speech(agent_isp, text: str) -> None:
    """Push IVR <Say> text directly into the agent's event queue."""
    if agent_isp._inject is not None:
        agent_isp._inject(FluxEndOfTurnEvent(transcript=text))
```

### Metrics Report Output
```python
# Source: click.echo pattern from cli.py _make_observer
import click

def print_metrics_report(results: list["ScenarioResult"]) -> None:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    click.echo(f"\n{'='*60}")
    click.echo(f"  Benchmark Results: {passed}/{total} passed")
    click.echo(f"{'='*60}")
    click.echo(f"  {'ID':<30} {'Pass':<6} {'Turns':<7} {'DTMF':<8} {'Latency'}")
    click.echo(f"  {'-'*54}")
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        dtmf = r.dtmf_accuracy_pct
        click.echo(
            f"  {r.scenario_id:<30} {status:<6} {r.turns:<7} "
            f"{dtmf:>5.0f}%   {r.wall_clock_s:.2f}s"
        )
    click.echo(f"{'='*60}")
    avg_turns = sum(r.turns for r in results) / total if total else 0
    avg_latency = sum(r.wall_clock_s for r in results) / total if total else 0
    click.echo(f"  Success rate: {passed/total*100:.0f}%  Avg turns: {avg_turns:.1f}  Avg latency: {avg_latency:.2f}s")
```

### IVR Server Health Check (Readiness Wait)
```python
# Source: httpx pattern; avoids fixed sleep
import httpx
import asyncio

async def _wait_for_ivr_ready(base_url: str, timeout: float = 10.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient() as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                r = await client.get(f"{base_url}/health")
                if r.status_code == 200:
                    return
            except httpx.ConnectError:
                pass
            await asyncio.sleep(0.05)
    raise TimeoutError(f"IVR server at {base_url} did not become ready in {timeout}s")
```

### Mock Flux/TTS Pools for Benchmark (No Real API Calls)
```python
# Source: shuo/tests/test_ivr_barge_in.py MockFluxPool + MockTTSPool pattern
class _BenchFluxPool:
    """No-op FluxPool: never fires turn events (IVR driver injects directly)."""
    async def get(self, on_end_of_turn, on_start_of_turn, **_):
        # Store callbacks so IVR driver can fire them
        self.on_end_of_turn = on_end_of_turn
        return self
    async def stop(self): pass
    async def send(self, _): pass

class _BenchTTSPool:
    """No-op TTSPool: discards all TTS requests (no audio in benchmark)."""
    async def start(self): pass
    async def stop(self): pass
    async def get(self, on_audio, on_done):
        from unittest.mock import AsyncMock, MagicMock
        tts = AsyncMock()
        tts.bind = MagicMock()
        return tts
    @property
    def available(self): return 1
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Single global ISP | ISP Protocol + LocalISP (Phase 1) | Phase 1 complete | Benchmark can use LocalISP without Twilio |
| Hardcoded DTMF injection | `_inject` callable set externally | Phase 1 (decision logged in STATE.md) | IVR driver can intercept DTMF without modifying LocalISP |
| No CLI bench command | `bench` stub in cli.py (Phase 3) | Phase 3 complete | Phase 4 replaces stub body only |
| IVR barge-in suppression via `ivr_mode` | `ivr_mode=lambda: True` param | Phase 2/3 | Benchmark must pass `ivr_mode=True` to suppress opener and barge-in |

---

## Open Questions

1. **IVR server shutdown between benchmark runs**
   - What we know: uvicorn daemon thread does not expose a clean stop API from outside
   - What's unclear: If `bench` is run repeatedly in the same process (e.g. tests), the old server thread may still hold the port
   - Recommendation: Use an ephemeral port (guarantees no conflict); accept that old threads keep running (daemon=True so they die with the process); document this limitation

2. **`ivr_mode` flag and IVR driver synchronization**
   - What we know: `ivr_mode=True` suppresses the agent's opening message and barge-in cancellation
   - What's unclear: The IVR driver must fire the first `FluxEndOfTurnEvent` (IVR greeting) before the agent speaks; timing depends on the IVR server startup completing before `run_conversation()` is called
   - Recommendation: Start IVR server and wait for `/health` before starting the agent loop

3. **DTMF accuracy metric definition**
   - What we know: BENCH-04 requires "DTMF accuracy"
   - What's unclear: Accuracy = (correct digits / expected digits)? Or binary pass/fail per scenario?
   - Recommendation: Define as `len(actual_dtmf) / len(expected_dtmf) * 100` if `dtmf_sequence` is set; 100% if no `dtmf_sequence` criterion (vacuously correct)

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio 1.3.0 |
| Config file | none — pytest auto-discovers |
| Quick run command | `cd shuo && python -m pytest tests/test_bench.py -x -q` |
| Full suite command | `cd shuo && python -m pytest tests/ -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| BENCH-01 | `load_scenarios()` parses valid YAML into `ScenarioConfig` objects | unit | `pytest tests/test_bench.py::test_load_scenarios -x` | ❌ Wave 0 |
| BENCH-01 | `load_scenarios()` raises on missing required fields | unit | `pytest tests/test_bench.py::test_load_scenarios_invalid -x` | ❌ Wave 0 |
| BENCH-01 | `ivr_flow` defaults to `example.yaml` when absent | unit | `pytest tests/test_bench.py::test_scenario_ivr_flow_default -x` | ❌ Wave 0 |
| BENCH-02 | `run_scenario()` creates LocalISP pair, starts IVR server, calls `run_conversation()` | integration | `pytest tests/test_bench.py::test_run_scenario_wires_localISP -x` | ❌ Wave 0 |
| BENCH-02 | Scenario completes without requiring DEEPGRAM/ELEVENLABS env vars | integration | `pytest tests/test_bench.py::test_bench_no_api_keys -x` | ❌ Wave 0 |
| BENCH-03 | `transcript_contains` passes when text present in collected transcript | unit | `pytest tests/test_bench.py::test_criterion_transcript_contains -x` | ❌ Wave 0 |
| BENCH-03 | `dtmf_sequence` passes when exact digits match | unit | `pytest tests/test_bench.py::test_criterion_dtmf_sequence -x` | ❌ Wave 0 |
| BENCH-03 | `max_turns` fails when turn count exceeds limit | unit | `pytest tests/test_bench.py::test_criterion_max_turns_exceeded -x` | ❌ Wave 0 |
| BENCH-03 | All criteria must pass for scenario to pass (AND logic) | unit | `pytest tests/test_bench.py::test_all_criteria_and -x` | ❌ Wave 0 |
| BENCH-04 | Metrics report includes success rate, avg turns, DTMF accuracy, latency | unit | `pytest tests/test_bench.py::test_metrics_report_fields -x` | ❌ Wave 0 |
| BENCH-04 | `voice-agent bench --dataset X` exits 0 with metrics output | integration | `pytest tests/test_bench.py::test_cli_bench_integration -x` | ❌ Wave 0 |
| BENCH-05 | 3 sample scenarios in `scenarios/example_ivr.yaml` are valid | unit | `pytest tests/test_bench.py::test_sample_scenarios_valid -x` | ❌ Wave 0 |
| BENCH-05 | All 3 scenarios pass when run against IVR mock | e2e | `pytest tests/test_bench.py::test_sample_scenarios_pass -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `cd shuo && python -m pytest tests/test_bench.py -x -q`
- **Per wave merge:** `cd shuo && python -m pytest tests/ -q`
- **Phase gate:** Full suite green (currently 57 pass, 2 pre-existing unrelated failures) before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `shuo/tests/test_bench.py` — all BENCH-01 through BENCH-05 tests (entire file is new)
- [ ] `shuo/shuo/bench.py` — benchmark runner module (new)
- [ ] `scenarios/example_ivr.yaml` — 3 sample scenarios (new file)

*(No shared fixtures needed — existing `test_ivr_barge_in.py` `MockISP`/`MockFluxPool`/`MockTTSPool` patterns will be inlined or extracted to conftest.)*

---

## Sources

### Primary (HIGH confidence)

- `shuo/shuo/conversation.py` — `run_conversation()` signature, `_inject` hook, `ivr_mode` param, observer event types
- `shuo/shuo/services/local_isp.py` — `LocalISP.pair()`, `send_dtmf()`, `_inject` callable contract
- `ivr/server.py` — FastAPI app, endpoint contracts, `reload_config()` test helper
- `ivr/engine.py` — TwiML URL patterns (`/ivr/step?node=ID`, `/ivr/gather?node=ID`)
- `ivr/config.py` — `IVRConfig`, `Node` dataclass, `parse_config()` YAML loading
- `ivr/flows/example.yaml` — actual node IDs for sample scenario authoring
- `shuo/shuo/cli.py` — `bench` stub body, `_run_local_call()` asyncio.wait pattern, `_make_observer()`
- `shuo/tests/test_ivr_barge_in.py` — MockISP, MockFluxPool, MockTTSPool patterns
- `shuo/tests/test_cli.py` — CliRunner patterns, existing `test_bench_stub` tests to update

### Secondary (MEDIUM confidence)

- `shuo/pyproject.toml` — confirmed httpx 0.28.1 + pyyaml in installed deps
- `shuo/shuo/types.py` — confirmed `FluxEndOfTurnEvent`, `DTMFToneEvent` dataclass signatures

### Tertiary (LOW confidence)

None — all claims are grounded in codebase reading.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new deps; all libraries verified in pyproject.toml and venv
- Architecture: HIGH — patterns directly derived from existing code in repo
- Pitfalls: HIGH — derived from actual code inspection (global engine state, _inject contract, conversation loop behavior)
- Test map: HIGH — test names map 1:1 to requirement acceptance criteria

**Research date:** 2026-03-21
**Valid until:** 2026-04-20 (stable codebase; no fast-moving dependencies)
