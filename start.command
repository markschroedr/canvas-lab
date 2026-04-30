#!/bin/zsh
set -e

cd "$(dirname "$0")"

PORT="${DESIGN_BRIDGE_PORT:-8787}"
URL="http://127.0.0.1:${PORT}/core/canvas.html?project=default"
HEALTH="http://127.0.0.1:${PORT}/health"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Canvas Lab needs python3 to run the local server."
  echo "Install Python 3, then double-click start.command again."
  exit 1
fi

if curl -fsS "$HEALTH" >/dev/null 2>&1; then
  echo "Canvas Lab is already running."
  echo "Opening $URL"
  open "$URL"
  exit 0
fi

echo "Starting Canvas Lab..."

if ! command -v codex >/dev/null 2>&1; then
  echo "Note: codex CLI was not found. The viewer still works, but Codex chat will not run until it is installed."
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "Note: claude CLI was not found. The viewer still works, but Claude chat will not run until it is installed."
fi

(
  for _ in {1..50}; do
    if curl -fsS "$HEALTH" >/dev/null 2>&1; then
      echo "Opening $URL"
      open "$URL"
      exit 0
    fi
    sleep 0.2
  done
  echo "Server started, but the health check did not respond in time."
  echo "Open manually: $URL"
) &

python3 bridge/server.py
