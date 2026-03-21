"""
Tests for the voice-agent CLI (serve, call, bench subcommands + YAML config).

Uses Click's CliRunner for isolated invocation and unittest.mock for
blocking I/O (uvicorn, threading, time.sleep, make_outbound_call).

NOTE: shuo.server imports 'dashboard' (a repo-root package not on the test
sys.path).  All tests that exercise serve/call must pre-inject fake modules for
'shuo.server', 'uvicorn', and related heavy deps before importing cli so that
the deferred imports inside the Click commands resolve to mocks.
"""

import sys
import types
import os
from unittest.mock import patch, MagicMock, call
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Lightweight fake modules injected into sys.modules before any test that
# exercises serve/call (both commands import shuo.server and uvicorn inside
# their function body).
# ---------------------------------------------------------------------------

def _make_fake_server_module():
    mod = types.ModuleType("shuo.server")
    mod.app = MagicMock(name="app")
    mod._draining = False
    mod._active_calls = 0
    return mod


def _make_fake_uvicorn():
    mod = types.ModuleType("uvicorn")
    mod.Config = MagicMock(name="uvicorn.Config")
    mod.Server = MagicMock(name="uvicorn.Server")
    return mod


class _ServerModuleContext:
    """Context manager that injects fake shuo.server + uvicorn into sys.modules."""

    def __init__(self):
        self._fake_server = None
        self._fake_uvicorn = None
        self._orig_server = None
        self._orig_uvicorn = None

    def __enter__(self):
        self._fake_server = _make_fake_server_module()
        self._fake_uvicorn = _make_fake_uvicorn()
        self._orig_server = sys.modules.get("shuo.server")
        self._orig_uvicorn = sys.modules.get("uvicorn")
        sys.modules["shuo.server"] = self._fake_server
        sys.modules["uvicorn"] = self._fake_uvicorn
        return self._fake_server, self._fake_uvicorn

    def __exit__(self, *args):
        if self._orig_server is None:
            sys.modules.pop("shuo.server", None)
        else:
            sys.modules["shuo.server"] = self._orig_server
        if self._orig_uvicorn is None:
            sys.modules.pop("uvicorn", None)
        else:
            sys.modules["uvicorn"] = self._orig_uvicorn


from shuo.cli import cli


# =============================================================================
# bench (no env / server needed)
# =============================================================================

def test_bench_requires_dataset():
    """bench exits with non-zero and error message when no --dataset given."""
    runner = CliRunner()
    result = runner.invoke(cli, ["bench"])
    assert result.exit_code != 0
    assert "dataset required" in (result.output + (result.stderr or "")).lower()


def test_bench_no_dataset():
    """bench exits with non-zero when no --dataset flag and no config."""
    runner = CliRunner()
    result = runner.invoke(cli, ["bench"])
    assert result.exit_code != 0


# =============================================================================
# YAML config loading
# =============================================================================

def test_config_file_loaded():
    """bench uses dataset from YAML config when no --dataset flag given."""
    from unittest.mock import AsyncMock
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open("voice-agent.yaml", "w") as f:
            f.write("bench:\n  dataset: from_config.yaml\n")
        with patch("shuo.bench.run_benchmark", new_callable=AsyncMock, return_value=[]) as mock_rb:
            result = runner.invoke(cli, ["bench"])
    assert result.exit_code == 0, result.output
    mock_rb.assert_called_once()
    call_args = mock_rb.call_args
    assert call_args[0][0] == "from_config.yaml"


def test_flag_overrides_config():
    """--dataset flag overrides the value from YAML config."""
    from unittest.mock import AsyncMock
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open("voice-agent.yaml", "w") as f:
            f.write("bench:\n  dataset: from_config.yaml\n")
        with patch("shuo.bench.run_benchmark", new_callable=AsyncMock, return_value=[]) as mock_rb:
            result = runner.invoke(cli, ["bench", "--dataset", "from_flag.yaml"])
    assert result.exit_code == 0, result.output
    mock_rb.assert_called_once()
    assert mock_rb.call_args[0][0] == "from_flag.yaml"


def test_config_auto_detect():
    """voice-agent.yaml in cwd is auto-loaded when --config is not specified."""
    from unittest.mock import AsyncMock
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open("voice-agent.yaml", "w") as f:
            f.write("bench:\n  dataset: auto_detected.yaml\n")
        with patch("shuo.bench.run_benchmark", new_callable=AsyncMock, return_value=[]) as mock_rb:
            result = runner.invoke(cli, ["bench"])
    assert result.exit_code == 0, result.output
    mock_rb.assert_called_once()
    assert mock_rb.call_args[0][0] == "auto_detected.yaml"


def test_explicit_config_flag():
    """--config path loads the specified file instead of auto-detect."""
    from unittest.mock import AsyncMock
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open("custom.yaml", "w") as f:
            f.write("bench:\n  dataset: custom_dataset.yaml\n")
        with patch("shuo.bench.run_benchmark", new_callable=AsyncMock, return_value=[]) as mock_rb:
            result = runner.invoke(cli, ["--config", "custom.yaml", "bench"])
    assert result.exit_code == 0, result.output
    mock_rb.assert_called_once()
    assert mock_rb.call_args[0][0] == "custom_dataset.yaml"


# =============================================================================
# serve
# =============================================================================

_FULL_ENV = {
    "TWILIO_ACCOUNT_SID": "x",
    "TWILIO_AUTH_TOKEN": "x",
    "TWILIO_PHONE_NUMBER": "x",
    "TWILIO_PUBLIC_URL": "http://example.com",
    "DEEPGRAM_API_KEY": "x",
    "GROQ_API_KEY": "x",
    "ELEVENLABS_API_KEY": "x",
}


def test_serve_starts_server():
    """serve starts a daemon server thread on the given port."""
    with _ServerModuleContext():
        with patch.dict(os.environ, _FULL_ENV), \
             patch("shuo.cli.threading.Thread") as mock_thread, \
             patch("shuo.cli.time.sleep", side_effect=[None, KeyboardInterrupt]):
            runner = CliRunner()
            runner.invoke(cli, ["serve", "--port", "9999"])
            # Thread should have been constructed
            mock_thread.assert_called_once()
            # daemon=True expected
            _, kwargs = mock_thread.call_args
            assert kwargs.get("daemon") is True


def test_serve_thread_started():
    """serve calls thread.start() to launch the server."""
    with _ServerModuleContext():
        with patch.dict(os.environ, _FULL_ENV), \
             patch("shuo.cli.threading.Thread") as mock_thread, \
             patch("shuo.cli.time.sleep", side_effect=[None, KeyboardInterrupt]):
            runner = CliRunner()
            runner.invoke(cli, ["serve", "--port", "9999"])
            mock_thread.return_value.start.assert_called_once()


def test_serve_env_check_fails():
    """serve exits with error when required env vars are missing.

    Patches load_dotenv so the .env file cannot repopulate env vars.
    """
    with _ServerModuleContext():
        runner = CliRunner()
        empty_env = {k: "" for k in [
            "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER",
            "TWILIO_PUBLIC_URL", "DEEPGRAM_API_KEY", "GROQ_API_KEY", "ELEVENLABS_API_KEY",
        ]}
        # Remove them so os.getenv returns None
        with patch("shuo.cli.load_dotenv"), \
             patch.dict(os.environ, {}, clear=True):
            result = runner.invoke(cli, ["serve"])
        assert result.exit_code != 0 or "Missing" in (result.output + (result.stderr or ""))


# =============================================================================
# call
# =============================================================================

def test_call_invokes_outbound():
    """call subcommand invokes make_outbound_call with the phone number."""
    fake_make_call = MagicMock(return_value="CA_FAKE_SID_123")
    fake_twilio_client = types.ModuleType("shuo.services.twilio_client")
    fake_twilio_client.make_outbound_call = fake_make_call

    with _ServerModuleContext(), \
         patch.dict(sys.modules, {"shuo.services.twilio_client": fake_twilio_client}), \
         patch.dict(os.environ, _FULL_ENV), \
         patch("shuo.cli.threading.Thread") as mock_thread, \
         patch("shuo.cli.time.sleep", side_effect=[None, None, KeyboardInterrupt]):
        runner = CliRunner()
        runner.invoke(cli, ["call", "+15551234567", "--goal", "test goal"])
        fake_make_call.assert_called_once_with("+15551234567")


def test_call_identity_prepended_to_goal():
    """call with --identity prepends 'You are {identity}.' to goal in CALL_GOAL."""
    fake_make_call = MagicMock(return_value="SID")
    fake_twilio_client = types.ModuleType("shuo.services.twilio_client")
    fake_twilio_client.make_outbound_call = fake_make_call

    with _ServerModuleContext(), \
         patch.dict(sys.modules, {"shuo.services.twilio_client": fake_twilio_client}), \
         patch.dict(os.environ, _FULL_ENV), \
         patch("shuo.cli.threading.Thread"), \
         patch("shuo.cli.time.sleep", side_effect=[None, None, KeyboardInterrupt]):
        runner = CliRunner()
        runner.invoke(cli, ["call", "+15551234567", "--goal", "check balance",
                             "--identity", "John Smith"])
        call_goal = os.environ.get("CALL_GOAL", "")
        assert "You are John Smith" in call_goal
        assert "check balance" in call_goal


# =============================================================================
# local-call
# =============================================================================

_LOCAL_CALL_ENV = {
    "DEEPGRAM_API_KEY": "x",
    "GROQ_API_KEY": "x",
    "ELEVENLABS_API_KEY": "x",
}


def test_local_call_help():
    """local-call --help shows all four flags and exits 0."""
    runner = CliRunner()
    result = runner.invoke(cli, ["local-call", "--help"])
    assert result.exit_code == 0, result.output
    assert "--caller-goal" in result.output
    assert "--callee-goal" in result.output
    assert "--caller-identity" in result.output
    assert "--callee-identity" in result.output


@patch("shuo.conversation.run_conversation")
@patch("shuo.services.local_isp.LocalISP")
@patch.dict(os.environ, _LOCAL_CALL_ENV)
def test_local_call_runs(mock_isp_cls, mock_run_conv):
    """local-call creates two ISP instances, pairs them, runs two conversations."""
    from unittest.mock import AsyncMock
    mock_run_conv.__class__ = AsyncMock
    mock_run_conv.side_effect = None
    mock_run_conv.return_value = None

    # Make run_conversation an async function that returns immediately
    async def _noop(*args, **kwargs):
        return None

    mock_run_conv.side_effect = _noop

    runner = CliRunner()
    result = runner.invoke(cli, [
        "local-call",
        "--caller-goal", "ask about balance",
        "--callee-goal", "answer questions",
    ])
    assert result.exit_code == 0, result.output
    assert mock_run_conv.call_count == 2
    assert mock_isp_cls.pair.called


@patch("shuo.conversation.run_conversation")
@patch("shuo.services.local_isp.LocalISP")
def test_local_call_config_merge(mock_isp_cls, mock_run_conv):
    """local-call reads caller/callee goals from YAML config when no flags given."""
    async def _noop(*args, **kwargs):
        return None

    mock_run_conv.side_effect = _noop

    runner = CliRunner()
    with runner.isolated_filesystem(), patch.dict(os.environ, _LOCAL_CALL_ENV):
        with open("voice-agent.yaml", "w") as f:
            f.write(
                "local_call:\n"
                "  caller:\n"
                "    goal: ask about balance\n"
                "  callee:\n"
                "    goal: answer questions\n"
            )
        result = runner.invoke(cli, ["local-call"])
    assert result.exit_code == 0, result.output
    assert mock_run_conv.call_count == 2

    # Extract the get_goal callbacks and verify their return values
    call_args_list = mock_run_conv.call_args_list
    caller_get_goal = call_args_list[0][1]["get_goal"]
    callee_get_goal = call_args_list[1][1]["get_goal"]
    assert caller_get_goal("dummy_sid") == "ask about balance"
    assert callee_get_goal("dummy_sid") == "answer questions"


@patch("shuo.conversation.run_conversation")
@patch("shuo.services.local_isp.LocalISP")
def test_local_call_flag_overrides_config(mock_isp_cls, mock_run_conv):
    """--caller-goal flag overrides the value from YAML config."""
    async def _noop(*args, **kwargs):
        return None

    mock_run_conv.side_effect = _noop

    runner = CliRunner()
    with runner.isolated_filesystem(), patch.dict(os.environ, _LOCAL_CALL_ENV):
        with open("voice-agent.yaml", "w") as f:
            f.write(
                "local_call:\n"
                "  caller:\n"
                "    goal: from config\n"
            )
        result = runner.invoke(cli, ["local-call", "--caller-goal", "from flag"])
    assert result.exit_code == 0, result.output

    call_args_list = mock_run_conv.call_args_list
    caller_get_goal = call_args_list[0][1]["get_goal"]
    assert caller_get_goal("dummy_sid") == "from flag"


@patch("shuo.cli.load_dotenv")
def test_local_call_env_check(mock_load_dotenv):
    """local-call exits with error when required env vars are missing."""
    runner = CliRunner()
    with patch.dict(os.environ, {}, clear=True):
        result = runner.invoke(cli, ["local-call", "--caller-goal", "x", "--callee-goal", "y"])
    assert result.exit_code != 0 or "Missing" in (result.output + (result.stderr or ""))


@patch("shuo.conversation.run_conversation")
@patch("shuo.services.local_isp.LocalISP")
@patch.dict(os.environ, _LOCAL_CALL_ENV)
def test_local_call_identity_in_goal(mock_isp_cls, mock_run_conv):
    """--caller-identity is folded into the goal string as 'You are {identity}.'"""
    async def _noop(*args, **kwargs):
        return None

    mock_run_conv.side_effect = _noop

    runner = CliRunner()
    result = runner.invoke(cli, [
        "local-call",
        "--caller-identity", "John",
        "--caller-goal", "check balance",
        "--callee-goal", "answer",
    ])
    assert result.exit_code == 0, result.output

    call_args_list = mock_run_conv.call_args_list
    caller_get_goal = call_args_list[0][1]["get_goal"]
    goal_str = caller_get_goal("dummy_sid")
    assert "You are John" in goal_str
    assert "check balance" in goal_str
