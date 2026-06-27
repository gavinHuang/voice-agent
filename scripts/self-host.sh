#!/usr/bin/env bash
# self-host.sh — Start voice-agent container + ngrok tunnel for self-hosting.
# Usage: ./scripts/self-host.sh [start|stop|status]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONTAINER_NAME="voice-agent"
AGENT_PORT=3040
NGROK_URL="https://jessi-foxlike-brielle.ngrok-free.dev"
LOG_DIR="$PROJECT_DIR/logs"

mkdir -p "$LOG_DIR"

start() {
  echo "▶  Starting self-hosted voice-agent..."

  # 1. Ensure colima is running
  if ! colima status &>/dev/null; then
    echo "  Starting colima..."
    colima start
  fi

  # 2. Stop existing container if running
  docker rm -f "$CONTAINER_NAME" &>/dev/null || true

  # 3. Start voice-agent container
  echo "  Starting voice-agent container (port $AGENT_PORT)..."
  docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    --env-file "$PROJECT_DIR/.env" \
    -p "$AGENT_PORT:$AGENT_PORT" \
    voice-agent:local

  # 4. Start ngrok tunnel (kill any existing)
  pkill -f "ngrok http.*$AGENT_PORT" &>/dev/null || true
  echo "  Starting ngrok tunnel → localhost:$AGENT_PORT..."
  nohup ngrok http "$AGENT_PORT" \
    --url="$(echo "$NGROK_URL" | sed 's|https://||')" \
    --log=stdout > "$LOG_DIR/ngrok.log" 2>&1 &

  # Wait for tunnel to be ready
  for i in $(seq 1 10); do
    if curl -s http://localhost:4040/api/tunnels &>/dev/null; then
      break
    fi
    sleep 1
  done

  echo "  ✓ voice-agent container: http://localhost:$AGENT_PORT"
  echo "  ✓ ngrok tunnel: $NGROK_URL"
  echo "  ✓ Logs: $LOG_DIR/ngrok.log"
  echo "  ✓ Container logs: docker logs -f $CONTAINER_NAME"
}

stop() {
  echo "▶  Stopping self-hosted voice-agent..."
  docker rm -f "$CONTAINER_NAME" &>/dev/null && echo "  ✓ Container stopped" || echo "  Container not running"
  pkill -f "ngrok http.*$AGENT_PORT" &>/dev/null && echo "  ✓ ngrok stopped" || echo "  ngrok not running"
}

status() {
  echo "── voice-agent container ──"
  docker inspect "$CONTAINER_NAME" --format='Status: {{.State.Status}}  Started: {{.State.StartedAt}}' 2>/dev/null || echo "Not running"
  echo ""
  echo "── ngrok tunnel ──"
  curl -s http://localhost:4040/api/tunnels 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
for t in data.get('tunnels', []):
    print(f\"URL: {t['public_url']} → {t['config']['addr']}\")
" 2>/dev/null || echo "Not running"
}

case "${1:-start}" in
  start)  start ;;
  stop)   stop ;;
  status) status ;;
  *)      echo "Usage: $0 [start|stop|status]"; exit 1 ;;
esac
