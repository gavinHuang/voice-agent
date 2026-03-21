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
@click.pass_context
def serve(ctx: click.Context, port: int | None, drain_timeout: int | None) -> None:
    """Start the FastAPI server and wait for inbound calls."""
    import uvicorn
    from shuo.server import app
    import shuo.server as server_module

    _check_env_vars()

    cfg = ctx.obj["config"].get("serve", {})
    effective_port = port if port is not None else cfg.get("port", int(os.getenv("PORT", "3040")))
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
    time.sleep(2)
    Logger.server_ready(os.getenv("TWILIO_PUBLIC_URL", ""))

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        Logger.shutdown()


@cli.command(name="call")
@click.argument("phone")
@click.option("--goal", type=str, default=None, help="Goal/instructions for the agent")
@click.option("--identity", type=str, default=None, help="Agent identity persona")
@click.pass_context
def call_cmd(ctx: click.Context, phone: str, goal: str | None, identity: str | None) -> None:
    """Initiate an outbound call to PHONE."""
    import uvicorn
    from shuo.server import app
    from shuo.services.twilio_client import make_outbound_call

    _check_env_vars()

    cfg = ctx.obj["config"].get("call", {})
    effective_goal = goal if goal is not None else cfg.get("goal", os.getenv("CALL_GOAL", ""))
    effective_identity = identity if identity is not None else cfg.get("identity", "")

    # Prepend identity to goal when provided
    if effective_identity:
        effective_goal = f"You are {effective_identity}. {effective_goal}"

    os.environ["CALL_GOAL"] = effective_goal

    def _start_server() -> None:
        config = uvicorn.Config(app, host="0.0.0.0", port=int(os.getenv("PORT", "3040")),
                                log_level="warning")
        server = uvicorn.Server(config)
        server.run()

    Logger.server_starting(int(os.getenv("PORT", "3040")))
    server_thread = threading.Thread(target=_start_server, daemon=True)
    server_thread.start()
    time.sleep(2)
    Logger.server_ready(os.getenv("TWILIO_PUBLIC_URL", ""))

    Logger.call_initiating(phone)
    call_sid = make_outbound_call(phone)
    Logger.call_initiated(call_sid)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        Logger.shutdown()


@cli.command()
@click.option("--dataset", type=str, default=None, help="YAML scenario file")
@click.pass_context
def bench(ctx: click.Context, dataset: str | None) -> None:
    """Run benchmark suite (stub — Phase 4)."""
    cfg = ctx.obj["config"].get("bench", {})
    effective_dataset = dataset if dataset is not None else cfg.get("dataset")
    click.echo(f"bench: dataset={effective_dataset!r}")
    click.echo("Benchmark runner not yet implemented (Phase 4).")


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
    from shuo.services.local_isp import LocalISP
    from shuo.conversation import run_conversation

    isp_caller = LocalISP()
    isp_callee = LocalISP()
    LocalISP.pair(isp_caller, isp_callee)

    caller_goal = _build_goal(caller_cfg.get("goal", ""), caller_cfg.get("identity", ""))
    callee_goal = _build_goal(callee_cfg.get("goal", ""), callee_cfg.get("identity", ""))

    task_caller = asyncio.create_task(
        run_conversation(
            isp_caller,
            observer=_make_observer("CALLER"),
            get_goal=lambda _: caller_goal,
        )
    )
    task_callee = asyncio.create_task(
        run_conversation(
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


def main() -> None:
    """Entry point for the voice-agent CLI."""
    cli()
