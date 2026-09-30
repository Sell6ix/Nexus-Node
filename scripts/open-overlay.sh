#!/usr/bin/env bash
# Always-on-top PC stats HUD (hub must be running).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${NEXUS_WEB_PORT:-8743}"
URL="${NEXUS_PUBLIC_URL:-http://127.0.0.1:$PORT}"
CORNER="${1:-top-right}"
cd "$ROOT"
exec python -m core.overlay_window --url "$URL" --corner "$CORNER"
