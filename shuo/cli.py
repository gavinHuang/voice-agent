"""
voice-agent CLI — Click group with serve, call, and bench subcommands.

Entry point: `voice-agent` (declared in pyproject.toml).
Config: loads voice-agent.yaml from cwd automatically; --config overrides.
"""

import asyncio
import os
import sys
import signal
import threading
import time
from pathlib import Path


# Add the project root (parent of the shuo/ package dir) to sys.path so that
# the sibling packages dashboard/ and ivr/ are importable when running via pipx
# or any other environment that only installed the shuo package.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import click
import yaml
from dotenv import load_dotenv

from shuo.log import setup_logging, Logger, get_logger

_REQUIRED_ENV_VARS = [
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_PHONE_NUMBER",
    "TWILIO_PUBLIC_URL",
    "DEEPGRAM_API_KEY",
    "GROQ_API_KEY",
    "ELEVENLABS_API_KEY",
]


def _load_config(config_path: str | None) -> dict:
    """Load YAML config file.

    If config_path is given, load it (exit on missing/bad YAML).
    If config_path is None, auto-detect voice-agent.yaml in cwd.
    Returns {} if no config found.
    """
    if config_path is not None:
        try:
            with open(config_path) as f:
                data = yaml.safe_load(f)
        except FileNotFoundError:
            click.echo(f"Error: config file not found: {config_path}", err=True)
            sys.exit(1)
        except yaml.YAMLError as exc:
            click.echo(f"Error: invalid YAML in {config_path}: {exc}", err=True)
            sys.exit(1)
        return data or {}

    # Auto-detect voice-agent.yaml in cwd
    auto_path = os.path.join(os.getcwd(), "voice-agent.yaml")
    if os.path.exists(auto_path):
        try:
            with open(auto_path) as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            click.echo(f"Error: invalid YAML in {auto_path}: {exc}", err=True)
            sys.exit(1)
        return data or {}

    return {}


def _check_env_vars() -> None:
    """Check required environment variables; exit with error if any missing."""
    missing = [v for v in _REQUIRED_ENV_VARS if not os.getenv(v)]
    if missing:
        click.echo(f"Missing required environment variables: {', '.join(missing)}", err=True)
        sys.exit(1)


_SOFTPHONE_REQUIRED_ENV_VARS = [
    "TWILIO_ACCOUNT_SID",
    "TWILIO_API_KEY",
    "TWILIO_API_SECRET",
]


def _check_softphone_env_vars() -> None:
    """Check env vars required for the browser softphone (no agent needed)."""
    missing = [v for v in _SOFTPHONE_REQUIRED_ENV_VARS if not os.getenv(v)]
    if missing:
        click.echo(f"Missing required environment variables: {', '.join(missing)}", err=True)
        sys.exit(1)


def _wait_for_ready(port: int, timeout: int = 120) -> None:
    """Poll /ready until warmup completes (TTS model + voice pool loaded)."""
    import urllib.request
    import urllib.error
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://localhost:{port}/ready", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(0.5)
    click.echo("Warning: server warmup timed out — proceeding anyway", err=True)


def _start_ngrok(port: int, env_var: str = "TWILIO_PUBLIC_URL") -> str:
    """Start an ngrok HTTP tunnel, write the public URL into env_var. Returns the URL."""
    try:
        from pyngrok import ngrok
    except ImportError:
        click.echo(
            "Error: pyngrok not installed. Run: pip install pyngrok",
            err=True,
        )
        sys.exit(1)
    auth_token = os.getenv("NGROK_AUTHTOKEN") or os.getenv("NGROK_AUTH_TOKEN")
    if auth_token:
        ngrok.set_auth_token(auth_token)
    tunnel = ngrok.connect(port, "http")
    public_url = tunnel.public_url
    # Prefer https (ngrok always supports it)
    if public_url.startswith("http://"):
        public_url = "https://" + public_url[7:]
    os.environ[env_var] = public_url
    click.echo(f"ngrok tunnel ({env_var}): {public_url}")
    return public_url


@click.group()
@click.option("--config", "-c", type=click.Path(exists=False), default=None,
              help="Path to YAML config file")
@click.pass_context
def cli(ctx: click.Context, config: str | None) -> None:
    """voice-agent — LLM-powered telephony agent."""
    ctx.ensure_object(dict)
    load_dotenv()
    setup_logging()
    ctx.obj["config"] = _load_config(config)


@cli.command()
@click.option("--port", type=int, default=None, help="Port to listen on")
@click.option("--drain-timeout", type=int, default=None,
              help="Seconds to wait for active calls before forced shutdown")
@click.option("--ngrok", "use_ngrok", is_flag=True, default=False,
              help="Start an ngrok tunnel and set TWILIO_PUBLIC_URL automatically")
@click.pass_context
def serve(ctx: click.Context, port: int | None, drain_timeout: int | None, use_ngrok: bool) -> None:
    """Start the FastAPI server and wait for inbound calls."""
    import uvicorn
    from shuo.web import app
    import shuo.web as server_module

    cfg = ctx.obj["config"].get("serve", {})
    effective_port = port if port is not None else cfg.get("port", int(os.getenv("PORT", "3040")))

    if use_ngrok:
        ngrok_url = _start_ngrok(effective_port)  # sets TWILIO_PUBLIC_URL
        # IVR mock is mounted at /ivr-mock on the same server; auto-derive its base URL
        if not os.getenv("IVR_BASE_URL"):
            os.environ["IVR_BASE_URL"] = f"{ngrok_url}/ivr-mock"
            click.echo(f"IVR_BASE_URL (auto): {os.environ['IVR_BASE_URL']}")

    _check_env_vars()
    effective_drain = (
        drain_timeout if drain_timeout is not None
        else cfg.get("drain_timeout", int(os.getenv("DRAIN_TIMEOUT", "300")))
    )

    _uvicorn_server: list = [None]  # mutable container so closure can mutate

    def _start_server() -> None:
        config = uvicorn.Config(app, host="0.0.0.0", port=effective_port, log_level="warning")
        server = uvicorn.Server(config)
        _uvicorn_server[0] = server
        server.run()

    def _handle_sigterm(signum, frame):
        logger = get_logger("shuo.cli")
        logger.info("SIGTERM received — starting graceful drain")
        server_module._draining = True

        if server_module._active_calls <= 0:
            logger.info("No active calls — shutting down now")
            if _uvicorn_server[0]:
                _uvicorn_server[0].should_exit = True
            return

        logger.info(
            f"Waiting up to {effective_drain}s for {server_module._active_calls} "
            f"active call(s) to finish..."
        )
        deadline = time.monotonic() + effective_drain
        while server_module._active_calls > 0 and time.monotonic() < deadline:
            time.sleep(1)

        remaining = server_module._active_calls
        if remaining > 0:
            logger.warning(f"Drain timeout — {remaining} call(s) still active, forcing exit")
        else:
            logger.info("All calls drained — shutting down cleanly")

        if _uvicorn_server[0]:
            _uvicorn_server[0].should_exit = True

    signal.signal(signal.SIGTERM, _handle_sigterm)

    Logger.server_starting(effective_port)
    server_thread = threading.Thread(target=_start_server, daemon=True)
    server_thread.start()
    _wait_for_ready(effective_port)
    Logger.server_ready(os.getenv("TWILIO_PUBLIC_URL", ""))

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        Logger.shutdown()


@cli.command(name="call")
@click.argument("phone")
@click.option("--goal",             type=str,  default=None, help="Goal/instructions for the agent")
@click.option("--context",  "context_file",
              type=click.Path(exists=False), default=None,
              help="YAML file providing CallContext field values")
@click.option("--agent-name",       type=str,  default=None, help="Name the agent introduces itself with")
@click.option("--agent-role",       type=str,  default=None, help="Role the agent describes itself as")
@click.option("--agent-tone",       type=str,  default=None, help="Tone/style instruction for the agent")
@click.option("--caller-name",      type=str,  default=None, help="Name of the person being called")
@click.option("--caller-context",   type=str,  default=None, help="Known facts about the caller")
@click.option("--constraint",       "constraints",
              type=str, multiple=True,
              help="Instruction the agent must follow (repeatable)")
@click.option("--success-criteria", type=str,  default=None, help="How the agent knows the call succeeded")
@click.option("--yes", "-y",        is_flag=True, default=False,
              help="Skip interactive confirmation and dial immediately")
@click.option("--ngrok", "use_ngrok", is_flag=True, default=False,
              help="Start an ngrok tunnel and set TWILIO_PUBLIC_URL automatically")
@click.pass_context
def call_cmd(
    ctx: click.Context,
    phone: str,
    goal: str | None,
    context_file: str | None,
    agent_name: str | None,
    agent_role: str | None,
    agent_tone: str | None,
    caller_name: str | None,
    caller_context: str | None,
    constraints: tuple,
    success_criteria: str | None,
    yes: bool,
    use_ngrok: bool,
) -> None:
    """Initiate an outbound call to PHONE."""
    import uvicorn
    from shuo.web import app
    from shuo.phone import dial_out
    from shuo.context import CallContext, load_identity_file, build_system_prompt, confirm_context

    if use_ngrok:
        agent_port = int(os.getenv("PORT", "3040"))
        ngrok_url = _start_ngrok(agent_port)  # sets TWILIO_PUBLIC_URL
        if not os.getenv("IVR_BASE_URL"):
            os.environ["IVR_BASE_URL"] = f"{ngrok_url}/ivr-mock"
            click.echo(f"IVR_BASE_URL (auto): {os.environ['IVR_BASE_URL']}")

    _check_env_vars()

    cfg = ctx.obj["config"].get("call", {})
    sources: dict
    # ── 1. Load identity.md (lowest precedence) ──────────────────────
    identity_fields, identity_src = load_identity_file(Path(os.getcwd()))
    if identity_src:
        for fname in identity_fields:
            sources[fname] = identity_src

    # ── 2. Base defaults: identity.md → config file → env ────────────
    ctx_fields: dict = {
        "goal":              cfg.get("goal", os.getenv("CALL_GOAL", "")),
        "agent_name":        identity_fields.get("agent_name",       "Alex"),
        "agent_role":        identity_fields.get("agent_role",       "a professional assistant"),
        "agent_tone":        identity_fields.get("agent_tone",       "friendly and concise"),
        "agent_background":  identity_fields.get("agent_background"),
        "caller_name":       None,
        "caller_context":    None,
        "constraints":       [],
        "success_criteria":  None,
    }

    # ── 3. Context YAML overrides identity / defaults ─────────────────
    if context_file:
        if not os.path.exists(context_file):
            click.echo(f"Error: context file not found: {context_file}", err=True)
            sys.exit(1)
        try:
            file_ctx = CallContext.from_yaml(context_file)
        except Exception as e:
            click.echo(f"Error: could not load context file: {e}", err=True)
            sys.exit(1)
        yaml_src = os.path.basename(context_file)
        for fname in ("goal", "agent_name", "agent_role", "agent_tone",
                      "agent_background", "caller_name", "caller_context",
                      "constraints", "success_criteria"):
            val = getattr(file_ctx, fname)
            default_val = {
                "goal": "", "agent_name": "Alex",
                "agent_role": "a professional assistant",
                "agent_tone": "friendly and concise",
                "agent_background": None, "caller_name": None,
                "caller_context": None, "constraints": [], "success_criteria": None,
            }[fname]
            if val != default_val:
                ctx_fields[fname] = val
                sources[fname] = yaml_src

    # ── 4. Explicit CLI flags (highest precedence) ────────────────────
    if goal is not None:
        ctx_fields["goal"] = goal
        sources.pop("goal", None)
    if agent_name is not None:
        ctx_fields["agent_name"] = agent_name
        sources["agent_name"] = "CLI flag"
    if agent_role is not None:
        ctx_fields["agent_role"] = agent_role
        sources["agent_role"] = "CLI flag"
    if agent_tone is not None:
        ctx_fields["agent_tone"] = agent_tone
        sources["agent_tone"] = "CLI flag"
    if caller_name is not None:
        ctx_fields["caller_name"] = caller_name
        sources["caller_name"] = "CLI flag"
    if caller_context is not None:
        ctx_fields["caller_context"] = caller_context
        sources["caller_context"] = "CLI flag"
    if constraints:
        ctx_fields["constraints"] = list(constraints)
        sources["constraints"] = "CLI flag"
    if success_criteria is not None:
        ctx_fields["success_criteria"] = success_criteria
        sources["success_criteria"] = "CLI flag"

    # ── 5. Build CallContext (goal may be empty — confirm_context handles it) ──
    if ctx_fields["goal"]:
        call_ctx = CallContext(
            goal=ctx_fields["goal"],
            agent_name=ctx_fields["agent_name"],
            agent_role=ctx_fields["agent_role"],
            agent_tone=ctx_fields["agent_tone"],
            agent_background=ctx_fields["agent_background"],
            caller_name=ctx_fields["caller_name"],
            caller_context=ctx_fields["caller_context"],
            constraints=ctx_fields["constraints"],
            success_criteria=ctx_fields["success_criteria"],
        )
    else:
        # Goal not yet known — confirm_context will prompt for it
        call_ctx = CallContext._partial(
            agent_name=ctx_fields["agent_name"],
            agent_role=ctx_fields["agent_role"],
            agent_tone=ctx_fields["agent_tone"],
            agent_background=ctx_fields["agent_background"],
            caller_name=ctx_fields["caller_name"],
            caller_context=ctx_fields["caller_context"],
            constraints=ctx_fields["constraints"],
            success_criteria=ctx_fields["success_criteria"],
        )

    # ── 6. Pre-call confirmation ──────────────────────────────────────
    call_ctx = confirm_context(call_ctx, yes=yes, sources=sources)

    # ── 7. Store assembled context as CALL_GOAL for the server ────────
    os.environ["CALL_GOAL"] = build_system_prompt(call_ctx)

    def _start_server() -> None:
        config = uvicorn.Config(app, host="0.0.0.0", port=int(os.getenv("PORT", "3040")),
                                log_level="warning")
        server = uvicorn.Server(config)
        server.run()

    Logger.server_starting(int(os.getenv("PORT", "3040")))
    server_thread = threading.Thread(target=_start_server, daemon=True)
    server_thread.start()
    _wait_for_ready(int(os.getenv("PORT", "3040")))
    Logger.server_ready(os.getenv("TWILIO_PUBLIC_URL", ""))

    Logger.call_initiating(phone)
    call_sid = dial_out(phone)
    Logger.call_initiated(call_sid)

    import shuo.web as _web_module

    # Wait for call to connect (up to 60s)
    deadline = time.monotonic() + 60
    while _web_module._active_calls == 0 and time.monotonic() < deadline:
        time.sleep(0.5)

    if _web_module._active_calls == 0:
        click.echo("Warning: call did not connect within 60 seconds", err=True)
    else:
        # Wait for call to end
        try:
            while _web_module._active_calls > 0:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass

    Logger.shutdown()


@cli.command()
@click.option("--dataset", type=str, default=None, help="YAML scenario file")
@click.option("--output", type=str, default=None, help="JSON output file for results (IVR mode only)")
@click.option(
    "--mode",
    type=click.Choice(["ivr", "two-agent"]),
    default="ivr",
    show_default=True,
    help="Benchmark mode: ivr (default) or two-agent",
)
@click.option(
    "--summary",
    type=str,
    default="reports/bench_summary.md",
    show_default=True,
    help="Path for shared cumulative summary Markdown file (two-agent mode)",
)
@click.pass_context
def bench(
    ctx: click.Context,
    dataset: str | None,
    output: str | None,
    mode: str,
    summary: str,
) -> None:
    """Run benchmark scenarios (IVR or two-agent mode)."""
    cfg = ctx.obj["config"].get("bench", {})
    effective_dataset = dataset if dataset is not None else cfg.get("dataset")
    if not effective_dataset:
        click.echo("Error: --dataset required (or set bench.dataset in config)", err=True)
        sys.exit(1)
    if mode == "two-agent":
        from shuo.bench import run_two_agent_benchmark
        asyncio.run(run_two_agent_benchmark(effective_dataset, summary_path=summary))
    else:
        from shuo.bench import run_benchmark
        asyncio.run(run_benchmark(effective_dataset, output_path=output))


def _make_observer(label: str):
    """Create an observer callback that prints transcript lines with a speaker label."""
    def observer(event: dict):
        if event.get("type") == "transcript":
            click.echo(f"[{label}]: {event['text']}", nl=True)
        elif event.get("type") == "agent_token":
            pass  # Skip streaming tokens for terminal output
    return observer


def _build_goal(goal: str, identity: str) -> str:
    """Combine identity and goal into a single goal string for the LLM."""
    if identity:
        return f"You are {identity}. {goal}"
    return goal


async def _run_local_call(caller_cfg: dict, callee_cfg: dict) -> None:
    """Run two concurrent conversations via LocalISP and wait for the first to complete."""
    from shuo.phone import LocalPhone
    from shuo.call import run_call

    isp_caller = LocalPhone()
    isp_callee = LocalPhone()
    LocalPhone.pair(isp_caller, isp_callee)

    caller_goal = _build_goal(caller_cfg.get("goal", ""), caller_cfg.get("identity", ""))
    callee_goal = _build_goal(callee_cfg.get("goal", ""), callee_cfg.get("identity", ""))

    task_caller = asyncio.create_task(
        run_call(
            isp_caller,
            observer=_make_observer("CALLER"),
            get_goal=lambda _: caller_goal,
        )
    )
    task_callee = asyncio.create_task(
        run_call(
            isp_callee,
            observer=_make_observer("CALLEE"),
            get_goal=lambda _: callee_goal,
        )
    )

    done, pending = await asyncio.wait([task_caller, task_callee], return_when=asyncio.FIRST_COMPLETED)

    for t in pending:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    click.echo("\n[CALL ENDED]")

    for t in done:
        if t.exception():
            raise t.exception()


_LOCAL_CALL_REQUIRED_ENV_VARS = [
    "DEEPGRAM_API_KEY",
    "GROQ_API_KEY",
    "ELEVENLABS_API_KEY",
]


def _check_local_call_env_vars() -> None:
    """Check env vars required for local-call (no Twilio needed)."""
    missing = [v for v in _LOCAL_CALL_REQUIRED_ENV_VARS if not os.getenv(v)]
    if missing:
        click.echo(f"Missing required environment variables: {', '.join(missing)}", err=True)
        sys.exit(1)


@cli.command("local-call")
@click.option("--caller-goal", type=str, default=None, help="Goal/instructions for the caller agent")
@click.option("--caller-identity", type=str, default=None, help="Identity persona for the caller agent")
@click.option("--callee-goal", type=str, default=None, help="Goal/instructions for the callee agent")
@click.option("--callee-identity", type=str, default=None, help="Identity persona for the callee agent")
@click.pass_context
def local_call(
    ctx: click.Context,
    caller_goal: str | None,
    caller_identity: str | None,
    callee_goal: str | None,
    callee_identity: str | None,
) -> None:
    """Run two LLM agents in a local call (no Twilio required)."""
    cfg = ctx.obj["config"].get("local_call", {})
    caller_cfg = dict(cfg.get("caller", {}))
    callee_cfg = dict(cfg.get("callee", {}))

    if caller_goal is not None:
        caller_cfg["goal"] = caller_goal
    if caller_identity is not None:
        caller_cfg["identity"] = caller_identity
    if callee_goal is not None:
        callee_cfg["goal"] = callee_goal
    if callee_identity is not None:
        callee_cfg["identity"] = callee_identity

    _check_local_call_env_vars()

    asyncio.run(_run_local_call(caller_cfg, callee_cfg))


_IVR_REQUIRED_ENV_VARS = [
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
]


def _check_ivr_env_vars() -> None:
    missing = [v for v in _IVR_REQUIRED_ENV_VARS if not os.getenv(v)]
    if missing:
        click.echo(f"Missing required environment variables: {', '.join(missing)}", err=True)
        sys.exit(1)


@cli.command("ivr-serve")
@click.option("--port", type=int, default=None, help="Port for the IVR server (default: 8001)")
@click.option("--ivr-config", type=click.Path(), default=None,
              help="Path to IVR flow YAML (overrides IVR_CONFIG env var)")
@click.option("--ngrok", "use_ngrok", is_flag=True, default=False,
              help="Start a dedicated ngrok tunnel and set IVR_BASE_URL automatically")
@click.pass_context
def ivr_serve(
    ctx: click.Context,
    port: int | None,
    ivr_config: str | None,
    use_ngrok: bool,
) -> None:
    """Start the IVR mock server as a standalone service on its own port.

    For two-server deployments where the IVR mock and the agent server each
    need a separate public URL (and thus separate ngrok tunnels).

    Twilio phone number for IVR → <IVR_BASE_URL>/twiml
    Twilio phone number for agent → <TWILIO_PUBLIC_URL>/twiml
    """
    import uvicorn

    cfg = ctx.obj["config"].get("ivr", {})
    effective_port = port if port is not None else cfg.get("port", int(os.getenv("IVR_PORT", "8001")))

    # Config file: CLI flag > config key > env var
    effective_config = ivr_config or cfg.get("config") or os.getenv("IVR_CONFIG", "")
    if effective_config:
        os.environ["IVR_CONFIG"] = effective_config

    if use_ngrok:
        ivr_url = _start_ngrok(effective_port, env_var="IVR_BASE_URL")
        click.echo(f"IVR entry point: {ivr_url}/twiml")
    elif not os.getenv("IVR_BASE_URL"):
        click.echo(
            "Warning: IVR_BASE_URL not set — TwiML callback URLs will be relative. "
            "Use --ngrok or set IVR_BASE_URL.",
            err=True,
        )

    _check_ivr_env_vars()

    # Import after env vars are set so IVR engine picks them up
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
    from ivr.server import app as ivr_app

    click.echo(f"IVR mock server starting on port {effective_port}")

    def _start_server() -> None:
        config = uvicorn.Config(ivr_app, host="0.0.0.0", port=effective_port, log_level="warning")
        server = uvicorn.Server(config)
        server.run()

    server_thread = threading.Thread(target=_start_server, daemon=True)
    server_thread.start()
    time.sleep(2)
    click.echo(f"IVR mock ready  (IVR_BASE_URL={os.getenv('IVR_BASE_URL', 'unset')})")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        Logger.shutdown()


@cli.command()
@click.option("--port", type=int, default=None, help="Port to listen on")
@click.option("--ngrok", "use_ngrok", is_flag=True, default=False,
              help="Start an ngrok tunnel and set TWILIO_PUBLIC_URL automatically")
@click.option("--no-browser", is_flag=True, default=False,
              help="Skip opening the browser automatically")
@click.pass_context
def softphone(
    ctx: click.Context,
    port: int | None,
    use_ngrok: bool,
    no_browser: bool,
) -> None:
    """Start the server and open the browser softphone at /phone."""
    import uvicorn
    import webbrowser
    from shuo.web import app

    cfg = ctx.obj["config"].get("serve", {})
    effective_port = port if port is not None else cfg.get("port", int(os.getenv("PORT", "3040")))

    if use_ngrok:
        _start_ngrok(effective_port)

    _check_softphone_env_vars()

    def _start_server() -> None:
        config = uvicorn.Config(app, host="0.0.0.0", port=effective_port, log_level="warning")
        server = uvicorn.Server(config)
        server.run()

    server_thread = threading.Thread(target=_start_server, daemon=True)
    server_thread.start()
    time.sleep(2)

    url = f"http://localhost:{effective_port}/phone"
    click.echo(f"Softphone: {url}")
    if not no_browser:
        webbrowser.open(url)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        Logger.shutdown()


def _fmt_config_entry(name: str, value: str | None, default: str | None = None) -> str:
    """Format a single config entry for display.

    Sensitive vars (KEY / TOKEN / SECRET / PASSWORD in name) are masked.
    Non-sensitive vars show their value, or '(not set)' if missing.
    """
    _SENSITIVE = ("KEY", "TOKEN", "SECRET", "PASSWORD", "ACCOUNT_SID")
    is_sensitive = any(s in name for s in _SENSITIVE)

    if value is None:
        shown = click.style("(not set)", fg="yellow") if not is_sensitive else click.style("(not set)", fg="yellow")
    elif is_sensitive:
        shown = click.style("[set]", fg="green")
    else:
        shown = value
        if default is not None and value == default:
            shown += click.style(f"  (default)", fg=8)  # dim

    return f"  {name:<32} {shown}"


def _section(title: str) -> str:
    bar = "─" * (50 - len(title) - 2)
    return click.style(f"── {title} {bar}", bold=True)


@cli.command("config")
@click.pass_context
def show_config(ctx: click.Context) -> None:
    """Show all configuration: URLs, phone numbers, ports, and whether secrets are set."""
    # Load .env so the values reflect what the server would actually use
    from dotenv import load_dotenv
    load_dotenv()

    e = os.environ.get  # shorthand

    sections = [
        (_section("Twilio — agent server"), [
            ("TWILIO_ACCOUNT_SID",   e("TWILIO_ACCOUNT_SID"),  None),
            ("TWILIO_AUTH_TOKEN",    e("TWILIO_AUTH_TOKEN"),    None),
            ("TWILIO_API_KEY",       e("TWILIO_API_KEY"),       None),
            ("TWILIO_API_SECRET",    e("TWILIO_API_SECRET"),    None),
            ("TWILIO_PHONE_NUMBER",  e("TWILIO_PHONE_NUMBER"),  None),
            ("TWILIO_PUBLIC_URL",    e("TWILIO_PUBLIC_URL"),    None),
            ("TWILIO_TWIML_APP_SID", e("TWILIO_TWIML_APP_SID"),None),
        ]),
        (_section("IVR mock server"), [
            ("IVR_BASE_URL",  e("IVR_BASE_URL"),   None),
            ("IVR_PORT",      e("IVR_PORT"),        "8001"),
            ("IVR_CONFIG",    e("IVR_CONFIG"),      None),
        ]),
        (_section("Agent server"), [
            ("PORT",                     e("PORT"),                     "3040"),
            ("DRAIN_TIMEOUT",            e("DRAIN_TIMEOUT"),            "300"),
            ("CALL_INACTIVITY_TIMEOUT",  e("CALL_INACTIVITY_TIMEOUT"),  "300"),
            ("CALL_GOAL",                e("CALL_GOAL"),                None),
            ("DASHBOARD_API_KEY",        e("DASHBOARD_API_KEY"),        None),
            ("CALL_RATE_LIMIT",          e("CALL_RATE_LIMIT"),          "10"),
        ]),
        (_section("LLM"), [
            ("GROQ_API_KEY",    e("GROQ_API_KEY"),    None),
            ("OPENAI_API_KEY",  e("OPENAI_API_KEY"),  None),
            ("LLM_MODEL",       e("LLM_MODEL"),       "groq:llama-3.3-70b-versatile"),
            ("LLM_MAX_TOKENS",  e("LLM_MAX_TOKENS"),  "500"),
            ("LLM_TEMPERATURE", e("LLM_TEMPERATURE"), "0.7"),
        ]),
        (_section("TTS"), [
            ("TTS_PROVIDER",            e("TTS_PROVIDER"),            "kokoro"),
            ("ELEVENLABS_API_KEY",      e("ELEVENLABS_API_KEY"),      None),
            ("ELEVENLABS_VOICE_ID",     e("ELEVENLABS_VOICE_ID"),     "21m00Tcm4TlvDq8ikWAM"),
            ("ELEVENLABS_MODEL",        e("ELEVENLABS_MODEL"),        "eleven_flash_v2_5"),
            ("FISH_AUDIO_URL",          e("FISH_AUDIO_URL"),          "http://localhost:8080"),
            ("FISH_AUDIO_REFERENCE_ID", e("FISH_AUDIO_REFERENCE_ID"), None),
            ("KOKORO_REPO_ID",          e("KOKORO_REPO_ID"),          "hexgrad/Kokoro-82M"),
            ("KOKORO_VOICE",            e("KOKORO_VOICE"),            "af_heart"),
        ]),
        (_section("STT"), [
            ("DEEPGRAM_API_KEY", e("DEEPGRAM_API_KEY"), None),
        ]),
        (_section("ngrok"), [
            ("NGROK_AUTH_TOKEN", e("NGROK_AUTH_TOKEN"), None),
        ]),
        (_section("Tracing"), [
            ("TRACE_MAX_FILES",     e("TRACE_MAX_FILES"),     "100"),
            ("TRACE_MAX_AGE_HOURS", e("TRACE_MAX_AGE_HOURS"), "24"),
        ]),
    ]

    for header, entries in sections:
        click.echo(header)
        for name, value, default in entries:
            click.echo(_fmt_config_entry(name, value, default))
        click.echo()


def _check_result(label: str, ok: bool, detail: str = "", warn: bool = False) -> None:
    """Print a single diagnostic result line."""
    if ok:
        icon = click.style("✓", fg="green", bold=True)
    elif warn:
        icon = click.style("!", fg="yellow", bold=True)
    else:
        icon = click.style("✗", fg="red", bold=True)
    suffix = f"  {click.style(detail, fg=8)}" if detail else ""
    click.echo(f"  {icon}  {label:<40}{suffix}")


@cli.command("diagnose")
@click.option("--skip-connectivity", is_flag=True, default=False,
              help="Only check that vars are set; skip live API calls")
@click.pass_context
def diagnose(ctx: click.Context, skip_connectivity: bool) -> None:
    """Check configuration and test connectivity to each service."""
    from dotenv import load_dotenv
    load_dotenv()
    e = os.environ.get

    any_fail = False

    # ── .env file ──────────────────────────────────────────────────────
    click.echo(_section(".env file"))
    env_path = Path(os.getcwd()) / ".env"
    if env_path.exists():
        _check_result(".env present", True, str(env_path))
    else:
        _check_result(".env present", False, "not found in cwd (values may come from shell env)")
        any_fail = True
    click.echo()

    # ── Twilio ─────────────────────────────────────────────────────────
    click.echo(_section("Twilio"))
    twilio_vars = [
        ("TWILIO_ACCOUNT_SID",  e("TWILIO_ACCOUNT_SID")),
        ("TWILIO_AUTH_TOKEN",   e("TWILIO_AUTH_TOKEN")),
        ("TWILIO_PHONE_NUMBER", e("TWILIO_PHONE_NUMBER")),
        ("TWILIO_PUBLIC_URL",   e("TWILIO_PUBLIC_URL")),
    ]
    twilio_ok = True
    for name, val in twilio_vars:
        if val:
            _check_result(f"{name} set", True)
        else:
            _check_result(f"{name} set", False, "missing")
            twilio_ok = False
            any_fail = True

    # Format check: account SID starts with AC
    sid = e("TWILIO_ACCOUNT_SID") or ""
    if sid and not sid.startswith("AC"):
        _check_result("TWILIO_ACCOUNT_SID format (starts AC)", False, f"got: {sid[:4]}...")
        twilio_ok = False
        any_fail = True
    elif sid:
        _check_result("TWILIO_ACCOUNT_SID format (starts AC)", True)

    if not skip_connectivity and twilio_ok:
        try:
            from twilio.rest import Client
            client = Client(e("TWILIO_ACCOUNT_SID"), e("TWILIO_AUTH_TOKEN"))
            account = client.api.accounts(e("TWILIO_ACCOUNT_SID")).fetch()
            _check_result("Twilio credentials valid", True, f"account: {account.friendly_name}")
        except Exception as exc:
            _check_result("Twilio credentials valid", False, str(exc)[:60])
            any_fail = True
    elif skip_connectivity:
        _check_result("Twilio credentials valid", True, "skipped", warn=False)
    click.echo()

    # ── Deepgram ───────────────────────────────────────────────────────
    click.echo(_section("Deepgram (STT)"))
    dg_key = e("DEEPGRAM_API_KEY")
    if dg_key:
        _check_result("DEEPGRAM_API_KEY set", True)
    else:
        _check_result("DEEPGRAM_API_KEY set", False, "missing")
        any_fail = True

    if not skip_connectivity and dg_key:
        try:
            import httpx
            resp = httpx.get(
                "https://api.deepgram.com/v1/projects",
                headers={"Authorization": f"Token {dg_key}"},
                timeout=8,
            )
            if resp.status_code == 200:
                _check_result("Deepgram API key valid", True)
            elif resp.status_code == 401:
                _check_result("Deepgram API key valid", False, "401 Unauthorized")
                any_fail = True
            else:
                _check_result("Deepgram API key valid", True,
                              f"HTTP {resp.status_code} (key accepted)", warn=False)
        except Exception as exc:
            _check_result("Deepgram connectivity", False, str(exc)[:60])
            any_fail = True
    elif skip_connectivity:
        _check_result("Deepgram API key valid", True, "skipped", warn=False)
    click.echo()

    # ── Groq (LLM) ────────────────────────────────────────────────────
    click.echo(_section("Groq (LLM)"))
    groq_key = e("GROQ_API_KEY")
    if groq_key:
        _check_result("GROQ_API_KEY set", True)
    else:
        _check_result("GROQ_API_KEY set", False, "missing")
        any_fail = True

    if not skip_connectivity and groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            models = client.models.list()
            model_ids = [m.id for m in models.data]
            default_model = e("LLM_MODEL") or "llama-3.3-70b-versatile"
            # Strip "groq:" prefix if present
            bare_model = default_model.removeprefix("groq:")
            if bare_model in model_ids:
                _check_result("Groq API key valid", True, f"model '{bare_model}' available")
            else:
                _check_result("Groq API key valid", True,
                              f"key valid but '{bare_model}' not listed — check LLM_MODEL",
                              warn=True)
        except Exception as exc:
            _check_result("Groq API key valid", False, str(exc)[:60])
            any_fail = True
    elif skip_connectivity:
        _check_result("Groq API key valid", True, "skipped", warn=False)
    click.echo()

    # ── ElevenLabs (TTS) ──────────────────────────────────────────────
    click.echo(_section("ElevenLabs (TTS)"))
    el_key = e("ELEVENLABS_API_KEY")
    tts_provider = e("TTS_PROVIDER") or "kokoro"
    if tts_provider != "elevenlabs":
        _check_result("ELEVENLABS_API_KEY set", el_key is not None,
                      f"TTS_PROVIDER={tts_provider} — ElevenLabs not active", warn=True)
    else:
        if el_key:
            _check_result("ELEVENLABS_API_KEY set", True)
        else:
            _check_result("ELEVENLABS_API_KEY set", False, "missing")
            any_fail = True

        if not skip_connectivity and el_key:
            try:
                import httpx
                resp = httpx.get(
                    "https://api.elevenlabs.io/v1/user",
                    headers={"xi-api-key": el_key},
                    timeout=8,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    tier = data.get("subscription", {}).get("tier", "unknown")
                    _check_result("ElevenLabs API key valid", True, f"tier: {tier}")
                elif resp.status_code == 401:
                    _check_result("ElevenLabs API key valid", False, "401 Unauthorized")
                    any_fail = True
                else:
                    _check_result("ElevenLabs API key valid", True,
                                  f"HTTP {resp.status_code}", warn=False)
            except Exception as exc:
                _check_result("ElevenLabs connectivity", False, str(exc)[:60])
                any_fail = True
        elif skip_connectivity:
            _check_result("ElevenLabs API key valid", True, "skipped", warn=False)
    click.echo()

    # ── Application ───────────────────────────────────────────────────
    click.echo(_section("Application"))

    # TWILIO_PUBLIC_URL: must be HTTPS and not localhost
    public_url = e("TWILIO_PUBLIC_URL") or ""
    if not public_url:
        _check_result("TWILIO_PUBLIC_URL is HTTPS", False, "not set")
        any_fail = True
    elif not public_url.startswith("https://"):
        _check_result("TWILIO_PUBLIC_URL is HTTPS", False, f"got: {public_url[:30]}")
        any_fail = True
    elif "localhost" in public_url or "127.0.0.1" in public_url:
        _check_result("TWILIO_PUBLIC_URL is HTTPS", False, "localhost not reachable by Twilio")
        any_fail = True
    else:
        _check_result("TWILIO_PUBLIC_URL is HTTPS", True, public_url[:50])

    # TWILIO_PHONE_NUMBER: E.164 format
    phone_num = e("TWILIO_PHONE_NUMBER") or ""
    if phone_num and not phone_num.startswith("+"):
        _check_result("TWILIO_PHONE_NUMBER E.164 format", False, "must start with +")
        any_fail = True
    elif phone_num:
        _check_result("TWILIO_PHONE_NUMBER E.164 format", True)

    # TTS provider importable
    tts = e("TTS_PROVIDER") or "kokoro"
    if tts == "kokoro":
        try:
            import importlib.util
            ok = importlib.util.find_spec("kokoro") is not None
        except Exception:
            ok = False
        if ok:
            _check_result("TTS provider (kokoro) importable", True)
        else:
            _check_result("TTS provider (kokoro) importable", False,
                          "run: uv add kokoro")
            any_fail = True
    elif tts == "fish":
        fish_url = e("FISH_API_URL") or e("FISH_SPEECH_API_URL")
        if not fish_url:
            _check_result("TTS provider (fish) FISH_API_URL set", False, "missing")
            any_fail = True
        else:
            _check_result("TTS provider (fish) FISH_API_URL set", True)
    elif tts == "elevenlabs":
        _check_result("TTS provider (elevenlabs) configured", e("ELEVENLABS_API_KEY") is not None,
                      "" if e("ELEVENLABS_API_KEY") else "ELEVENLABS_API_KEY missing")

    # Port available
    import socket
    server_port = int(e("PORT") or "3040")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", server_port))
        _check_result(f"Port {server_port} available", True)
    except OSError:
        _check_result(f"Port {server_port} available", False,
                      "already in use — stop existing server first", warn=False)
        any_fail = True

    click.echo()

    # ── Summary ───────────────────────────────────────────────────────
    if any_fail:
        click.echo(click.style("Some checks failed. Fix the issues above before running.", fg="red", bold=True))
        sys.exit(1)
    else:
        click.echo(click.style("All checks passed.", fg="green", bold=True))


def main() -> None:
    """Entry point for the voice-agent CLI."""
    cli()
