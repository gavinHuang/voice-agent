#!/usr/bin/env bash
set -e

PORT=3040

echo "Stopping server on port $PORT..."
pids=$(lsof -i :$PORT -t 2>/dev/null || true)
if [ -n "$pids" ]; then
  kill $pids 2>/dev/null
  sleep 1
  echo "Stopped."
else
  echo "No server running."
fi

echo "Starting server..."
cd "$(dirname "$0")/shuo"
PYTORCH_ENABLE_MPS_FALLBACK=1 .venv-kokoro/bin/python3 main.py 2>&1 | tee /tmp/voice-agent-server.log
