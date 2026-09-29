#!/usr/bin/env bash
# Start the Nexus Node web hub (runs first-run setup if needed).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
"$ROOT/scripts/setup.sh" --quiet
HOST="${NEXUS_WEB_HOST:-0.0.0.0}"
PORT="${NEXUS_WEB_PORT:-8743}"
EXTRA=()
if [[ "${NEXUS_WEB_TLS:-}" =~ ^(1|true|yes|on)$ ]]; then
  EXTRA+=(--tls)
fi
exec "$ROOT/.venv/bin/python" "$ROOT/main.py" --mode web --host "$HOST" --port "$PORT" "${EXTRA[@]}"
