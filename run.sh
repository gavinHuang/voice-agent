#!/usr/bin/env bash
# run.sh — shortcuts for voice-agent commands
# Usage: ./run.sh <command> [args]
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHUO_DIR="$SCRIPT_DIR/shuo"

# Load .env so script-level vars (ports, URLs) are available
if [ -f "$SHUO_DIR/.env" ]; then
  set -a; source "$SHUO_DIR/.env"; set +a
fi

AGENT_PORT="${PORT:-3040}"
IVR_PORT="${IVR_PORT:-8001}"
IVR_NGROK_URL="${IVR_BASE_URL:-https://jessi-foxlike-brielle.ngrok-free.dev}"

# Required for Kokoro TTS on Apple Silicon
export PYTORCH_ENABLE_MPS_FALLBACK=1

# Ensure project root (dashboard/, ivr/, etc.) is importable even when running
# via the pipx-installed voice-agent binary, whose __file__ is inside the venv.
export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"

# ── Helpers ───────────────────────────────────────────────────────────────────

_kill_port() {
  local port=$1
  local pids
  pids=$(lsof -i ":$port" -t 2>/dev/null || true)
  if [ -n "$pids" ]; then
    echo "  Killing PID(s) $pids on port $port"
    kill $pids 2>/dev/null || true
  fi
}

_wait_for_port() {
  local port=$1 timeout=10 i=0
  while ! lsof -i ":$port" -t &>/dev/null; do
    sleep 1; i=$((i+1))
    [ $i -ge $timeout ] && echo "  Warning: port $port did not open in ${timeout}s" && return
  done
}

# ── Commands ──────────────────────────────────────────────────────────────────

cmd_serve() {
  echo "▶  Agent server  (port $AGENT_PORT, ngrok auto)"
  cd "$SHUO_DIR"
  voice-agent serve --ngrok
}

cmd_ivr() {
  echo "▶  IVR mock server  (port $IVR_PORT)"
  echo "▶  Binding ngrok cloud endpoint: $IVR_NGROK_URL → localhost:$IVR_PORT"
  trap 'kill $(jobs -p) 2>/dev/null; exit 0' INT TERM
  ngrok http --url="$IVR_NGROK_URL" "$IVR_PORT" &
  sleep 2
  cd "$SHUO_DIR"
  voice-agent ivr-serve
}

cmd_all() {
  echo "▶  Starting agent + IVR"
  trap 'echo "Stopping all..."; kill $(jobs -p) 2>/dev/null; exit 0' INT TERM

  # IVR: bind cloud endpoint + start server in background
  echo "   [ivr] binding ngrok cloud endpoint → localhost:$IVR_PORT"
  ngrok http --url="$IVR_NGROK_URL" "$IVR_PORT" &>/tmp/voice-agent-ivr-ngrok.log &
  sleep 2

  echo "   [ivr] starting ivr-serve"
  cd "$SHUO_DIR"
  voice-agent ivr-serve &>/tmp/voice-agent-ivr.log &
  _wait_for_port "$IVR_PORT"

  # Agent: serve with auto ngrok
  echo "   [agent] starting serve --ngrok"
  voice-agent serve --ngrok 2>&1 | tee /tmp/voice-agent-server.log
}

cmd_softphone() {
  echo "▶  Softphone  (port $AGENT_PORT)"
  cd "$SHUO_DIR"
  voice-agent softphone
}

cmd_call() {
  local phone="${1:?Usage: ./run.sh call <phone_number>}"
  local goal="${2:-${CALL_GOAL:-}}"
  echo "▶  Calling $phone"
  [ -n "$goal" ] && echo "   Goal: $goal"
  cd "$SHUO_DIR"
  if [ -n "$goal" ]; then
    voice-agent call "$phone" --goal "$goal" --ngrok
  else
    voice-agent call "$phone" --ngrok
  fi
}

cmd_local_call() {
  local caller_goal="${1:-${CALLER_GOAL:-Navigate the IVR and reach the sales department}}"
  local callee_goal="${2:-${CALLEE_GOAL:-You are an IVR system. Say: Welcome. Press 1 for sales, 2 for support, 3 for billing.}}"
  echo "▶  Local call (no Twilio)"
  echo "   Caller: $caller_goal"
  echo "   Callee: $callee_goal"
  cd "$SHUO_DIR"
  voice-agent local-call \
    --caller-goal "$caller_goal" \
    --callee-goal "$callee_goal"
}

cmd_bench() {
  local dataset="${1:-${IVR_BENCH_DATASET:-$SCRIPT_DIR/scenarios/example_ivr.yaml}}"
  echo "▶  Benchmark  ($dataset)"
  cd "$SHUO_DIR"
  voice-agent bench --dataset "$dataset"
}

cmd_config() {
  cd "$SHUO_DIR"
  voice-agent config
}

cmd_stop() {
  echo "▶  Stopping servers"
  _kill_port "$AGENT_PORT"
  _kill_port "$IVR_PORT"
  echo "   Done."
}

cmd_logs() {
  local target="${1:-agent}"
  case "$target" in
    agent)  tail -f /tmp/voice-agent-server.log 2>/dev/null || echo "No agent log found." ;;
    ivr)    tail -f /tmp/voice-agent-ivr.log 2>/dev/null || echo "No IVR log found." ;;
    *)      tail -f /tmp/voice-agent-server.log /tmp/voice-agent-ivr.log 2>/dev/null ;;
  esac
}

# ── Usage ─────────────────────────────────────────────────────────────────────

usage() {
  cat <<EOF
Usage: ./run.sh <command> [args]

Commands:
  serve                  Start agent server with auto ngrok tunnel
  ivr                    Start IVR mock server + bind ngrok cloud endpoint
  all                    Start agent + IVR together (Ctrl+C to stop all)
  softphone              Start server and open browser softphone
  call <phone> [goal]    Make outbound call (goal optional, falls back to CALL_GOAL)
  local-call [cg] [bg]   Run two LLM agents locally — no Twilio required
                           cg = caller goal, bg = callee goal (or set CALLER/CALLEE_GOAL)
  bench [dataset.yaml]   Run IVR benchmark (default: scenarios/example_ivr.yaml)
  config                 Show all configuration (API keys masked)
  stop                   Kill agent + IVR servers by port
  logs [agent|ivr|both]  Tail server logs (default: agent)

IVR ngrok endpoint : $IVR_NGROK_URL
Agent port         : $AGENT_PORT
IVR port           : $IVR_PORT
EOF
}

# ── Dispatch ──────────────────────────────────────────────────────────────────

case "${1:-}" in
  serve)      cmd_serve ;;
  ivr)        cmd_ivr ;;
  all)        cmd_all ;;
  softphone)  cmd_softphone ;;
  call)       shift; cmd_call "$@" ;;
  local-call) shift; cmd_local_call "$@" ;;
  bench)      shift; cmd_bench "$@" ;;
  config)     cmd_config ;;
  stop)       cmd_stop ;;
  logs)       shift; cmd_logs "$@" ;;
  *)          usage; exit 1 ;;
esac
