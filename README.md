# Nexus Node

Personal assistant for **phone + PC** — multi-user web app, no Telegram required.

Each person has an account, their own AI API key, and can control devices on the hub network.

## How it works

```
Phone or PC browser (PWA)
        │
        ▼
  Web hub on a PC   ←── you run this
        │
        ├── local tools (this machine)
        └── workers on other PCs (optional)
```

- **Hub** (`--mode web`) — accounts, chat UI, agent, confirmations, reminders  
- **Worker** (`--mode worker`) — extra PC that exposes tools to the hub  
- First registered user is **owner**; others join with an **invite code** (or open signup)
- Chat history is saved per account on the hub (`data/chats/`) and restores after reload
- Web search + page fetch; contacts with confirmed email / Telegram send
- Personal memory, diary (dated journal), and a local hub calendar
- Voice chat: mic button (browser speech or Whisper), optional spoken replies
- Google Gmail/Calendar OAuth, daily briefing, app-open/daily reminders, chat file attach, owner backup zip

## Quick start

```bash
./scripts/setup.sh && ./scripts/start.sh          # Linux / macOS
.\scripts\setup.ps1; .\scripts\start.ps1          # Windows
```

Open the URL printed in the console. First account = owner. Paste your AI key in Settings.

API keys are encrypted on disk (`data/secrets.key` or `NEXUS_SECRETS_KEY`). Keep that file private.

Autostart on login:

```bash
./scripts/install-autostart-linux.sh
# Windows:  .\scripts\install-autostart-windows.ps1
```

### Phone away from home (Tailscale)

1. Install [Tailscale](https://tailscale.com) on the hub PC and the phone (same account).
2. Start the hub, copy the printed `http://100.x.x.x:8743` URL.
3. Open that URL on the phone. Add to Home Screen.
4. Do **not** port-forward 8743 to the public internet.

Closed-app reminder notifications need HTTPS:

```bash
python main.py --mode web --tls          # self-signed cert in data/certs/
# or Tailscale: tailscale serve --bg 8743
```

Trust `data/certs/cert.pem` once in the browser (Chrome will warn). Better: Tailscale Serve with a real cert.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Dev UI with hot reload:

```bash
# terminal 1
python main.py --mode web --host 127.0.0.1 --port 8743
# terminal 2
cd web && npm run dev
# open http://127.0.0.1:5174
```

## For other people on your hub

1. Owner → Settings → **Create invite**  
2. Friend opens the same URL → Join → paste invite → sets **their** API key  
3. Friends get a limited tool set; dangerous actions ask **them** to confirm in the app

Open signup (no invite): `NEXUS_OPEN_SIGNUP=true` in `.env`.

## Second PC

```env
DEVICE_ID=laptop
DEVICE_NAME=Laptop
WORKER_TOKEN=long-random-secret
WORKER_HOST=0.0.0.0
WORKER_PORT=8765
```

```bash
python main.py --mode worker
```

In the web app → Devices → add `http://192.168.x.x:8765` + token → **Home** / **Use**.

## Modes

| Mode | Command |
|------|---------|
| Web hub (default) | `python main.py` or `--mode web` |
| Worker | `python main.py --mode worker` |
| CLI | `python main.py --mode cli` |
| Legacy Telegram | `python main.py --mode telegram` |

## Security notes

- Bind `0.0.0.0` only on trusted LAN (or put behind VPN/Tailscale)  
- Each user stores their own API key on the hub disk, encrypted (`data/secrets.key`)  
- Confirm dangerous tools in the app UI  
- Prefer invite codes over open signup on shared networks  

## Layout

```
main.py          entrypoint
core/            agent, accounts, web hub, devices, worker…
tools/           LLM tools
web/             React PWA (Vite + Tailwind)
data/            accounts, devices, daily notes (gitignored)
```
