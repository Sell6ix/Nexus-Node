# Nexus Node

Daily personal assistant for **multiple people** and **multiple devices**.

Natural language → LLM tool calling → Python tools on the chosen device.

## What you get

- **BYOK** — each Telegram user sets their own AI key (`/apikey`), model, and OpenAI-compatible `/baseurl`
- **Multi-device** — hub PC runs Telegram; other PCs run `--mode worker`; pick with `/use`
- **Security** — owner-locked pairing, one-time `/invite` for friends, owner-only confirmations, shell/inbound gates, audit log, secret redaction
- **Daily tools** — notes, todos, reminders (+ system/ADB/Steam/desktop tools)

## Modes

| Mode | Command | Role |
|------|---------|------|
| CLI | `python main.py` | Local console on this PC |
| Telegram hub | `python main.py --mode telegram` | Phone control + device routing |
| Both | `python main.py --mode both` | CLI + Telegram |
| Worker | `python main.py --mode worker` | Expose this PC’s tools to the hub |

## Quick start (one PC + phone)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# set TELEGRAM_BOT_TOKEN, DEVICE_ID, DEVICE_NAME
python main.py --mode telegram
```

In Telegram:

```text
/whoami
/pair <code-from-console>
/apikey sk-...
/model gpt-4o-mini
What's on my todo list?
```

Put `TELEGRAM_OWNER_CHAT_ID=<your id>` into `.env` and restart.

## Second device (friend’s PC or your laptop)

On the **worker** PC:

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

On the **hub** (owner Telegram):

```text
/device_add laptop Laptop http://192.168.x.x:8765 long-random-secret
/use laptop
show system stats
```

Worker defaults to localhost; use `0.0.0.0` only on trusted LAN and keep the token secret. Dangerous confirmed tools still run on the hub for now.

## Invite a friend (their own key, limited tools)

Owner:

```text
/invite sam
```

Friend (privately):

```text
/pair <invite-code>
/apikey sk-friend-key
```

Friends cannot use `run_shell`, file push, steamcmd update, etc. Kill-process is owner-only. Dangerous actions still ask **you** (owner) for ✅/❌.

## Daily tools

`add_note` `list_notes` `add_todo` `list_todos` `complete_todo` `set_reminder` `list_reminders`

Plus existing ADB / Cursor / Steam / system / desktop tools.

## Security checklist

1. Keep bot username private  
2. Set `TELEGRAM_OWNER_CHAT_ID`  
3. Prefer `/invite` over `/unlock`  
4. Each person uses `/apikey` (keys stored under `data/users/`, never shared)  
5. Worker tokens are long and unique; prefer LAN-only bind  
6. Watch `/audit`

## Layout

```
main.py
core/          agent, users (BYOK), devices, worker, pairing, safety, audit…
tools/         plugins (auto-discovered)
data/          users, devices, inbox, audit, daily notes (gitignored)
scripts/       autostart helpers
```
