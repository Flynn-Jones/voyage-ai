#!/usr/bin/env bash
# Starts / stops the shared, NON-containerised AI servers on the host:
#   MCP server          :7001  ai-services/mcp-server/mcp_http_server.py
#   RAG server          :7002  ai-services/rag-server/rag_http_server.py
#   Multi-Agent Server  :7004  ai-services/multi-agent-server/server.py
#
# Usage (from the repo root; up-all.sh / down-all.sh call this for you):
#   ./scripts/ai-services.sh start
#   ./scripts/ai-services.sh stop
#   ./scripts/ai-services.sh status
#
# Uses ai-services/.venv (or ai-services/venv). PIDs and logs go to
# ai-services/.run/ (gitignored). A server that is already listening is left alone.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AI="$ROOT/ai-services"
RUN_DIR="$AI/.run"

# name|directory|entrypoint|port
SERVERS=(
  "mcp-server|mcp-server|mcp_http_server.py|7001"
  "rag-server|rag-server|rag_http_server.py|7002"
  "multi-agent-server|multi-agent-server|server.py|7004"
)

find_python() {
  for candidate in "$AI/.venv/bin/python" "$AI/.venv/Scripts/python.exe" \
                   "$AI/venv/bin/python" "$AI/venv/Scripts/python.exe"; do
    if [ -x "$candidate" ] && "$candidate" -c "import mcp" >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

listening() {
  # any HTTP answer counts; 127.0.0.1 avoids the slow ::1 attempt on Windows
  curl -s -m 3 -o /dev/null "http://127.0.0.1:$1/"
}

start() {
  local python
  if ! python="$(find_python)"; then
    echo "==> Skipping shared AI servers: no working ai-services/.venv or ai-services/venv."
    echo "    One-time setup: see 'Start the shared AI servers' in README.md"
    return 0
  fi
  mkdir -p "$RUN_DIR"
  for entry in "${SERVERS[@]}"; do
    IFS="|" read -r name dir script port <<<"$entry"
    if listening "$port"; then
      echo "==> $name already running on :$port"
      continue
    fi
    echo "==> Starting $name on :$port (log: ai-services/.run/$name.log)"
    (cd "$AI/$dir" && exec nohup "$python" -u "$script" >"$RUN_DIR/$name.log" 2>&1) &
    echo $! >"$RUN_DIR/$name.pid"
  done
}

stop() {
  for entry in "${SERVERS[@]}"; do
    IFS="|" read -r name _ _ port <<<"$entry"
    local pid_file="$RUN_DIR/$name.pid"
    if [ -f "$pid_file" ] && kill "$(cat "$pid_file")" 2>/dev/null; then
      echo "==> Stopped $name"
    elif listening "$port"; then
      echo "==> $name on :$port was not started by up-all.sh; stop it in its own terminal"
    fi
    rm -f "$pid_file"
  done
}

status() {
  for entry in "${SERVERS[@]}"; do
    IFS="|" read -r name _ _ port <<<"$entry"
    if listening "$port"; then echo "    $name :$port up"; else echo "    $name :$port down"; fi
  done
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
