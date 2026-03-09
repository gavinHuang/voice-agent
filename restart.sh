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
nohup python3 main.py > /tmp/voice-agent-server.log 2>&1 &
sleep 3

if lsof -i :$PORT -t > /dev/null 2>&1; then
  echo "Server running on port $PORT (PID: $(lsof -i :$PORT -t | head -1))"
else
  echo "Failed to start. Check /tmp/voice-agent-server.log"
  exit 1
fi
