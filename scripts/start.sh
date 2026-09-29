#!/usr/bin/env bash
# Start the Nexus Node web hub (builds the UI once if needed).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Creating venv…"
  python3 -m venv "$ROOT/.venv"
  PYTHON="$ROOT/.venv/bin/python"
  "$PYTHON" -m pip install -r "$ROOT/requirements.txt"
fi

if [[ ! -d "$ROOT/web/dist" ]]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm is required to build the web UI (web/dist is missing)."
    exit 1
  fi
  echo "Building web UI…"
  (cd "$ROOT/web" && npm install && npm run build)
fi

HOST="${NEXUS_WEB_HOST:-0.0.0.0}"
PORT="${NEXUS_WEB_PORT:-8743}"
exec "$PYTHON" "$ROOT/main.py" --mode web --host "$HOST" --port "$PORT"
