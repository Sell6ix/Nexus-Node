#!/usr/bin/env bash
# First-run: venv, deps, web UI, .env, encryption key.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
QUIET="${1:-}"

say() { if [[ "$QUIET" != "--quiet" ]]; then echo "$*"; fi }

PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Creating Python venv…"
  python3 -m venv "$ROOT/.venv"
  PYTHON="$ROOT/.venv/bin/python"
fi
echo "Installing Python packages…"
"$PYTHON" -m pip install -q -r "$ROOT/requirements.txt"

if [[ ! -d "$ROOT/web/dist" || "${NEXUS_REBUILD_UI:-}" == "1" ]]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm is required to build the web UI."
    exit 1
  fi
  echo "Building web UI…"
  (cd "$ROOT/web" && npm install --no-fund --no-audit && npm run build)
fi

if [[ ! -f "$ROOT/.env" && -f "$ROOT/.env.example" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "Wrote .env from .env.example — edit DEVICE_NAME if you want."
fi

"$PYTHON" - <<'PY'
from pathlib import Path
from core.secrets import default_box
box = default_box(Path("data"))
print(f"Secrets key ready ({box.key_path})")
PY

if [[ "$QUIET" != "--quiet" ]]; then
  echo
  echo "Setup complete."
  echo "  Start now:     $ROOT/scripts/start.sh"
  echo "  Autostart:     $ROOT/scripts/install-autostart-linux.sh"
  echo "  Then open the URL printed by the hub and create the owner account."
fi
