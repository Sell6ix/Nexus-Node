#!/usr/bin/env bash
# Install a systemd --user unit that starts the Nexus Node web hub on login.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="$ROOT/scripts/nexus-node.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_DST="$UNIT_DIR/nexus-node.service"

mkdir -p "$UNIT_DIR"

PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Missing venv at $PYTHON — create it first:"
  echo "  python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

if [[ ! -d "$ROOT/web/dist" ]]; then
  echo "Build the UI first:  cd web && npm install && npm run build"
  exit 1
fi

sed \
  -e "s|WorkingDirectory=.*|WorkingDirectory=$ROOT|" \
  -e "s|EnvironmentFile=.*|EnvironmentFile=-$ROOT/.env|" \
  -e "s|ExecStart=.*|ExecStart=$PYTHON $ROOT/main.py --mode web --host 0.0.0.0 --port 8743|" \
  "$UNIT_SRC" > "$UNIT_DST"

systemctl --user daemon-reload
systemctl --user enable --now nexus-node.service
systemctl --user status nexus-node.service --no-pager || true

echo
echo "Installed: $UNIT_DST"
echo "Logs: journalctl --user -u nexus-node.service -f"
echo "Open the URL printed in those logs (LAN or Tailscale 100.x)."
