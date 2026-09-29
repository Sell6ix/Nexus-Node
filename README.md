# Nexus Node

Personal PC assistant orchestrator: natural-language commands → LLM function calling → Python tools.

Works from **the PC console** and from a **phone via Telegram** (same agent, same tools, same confirmation gates).

## Features

- **Plugin tools** under `tools/` — auto-discovered, Pydantic v2 schemas, OpenAI function calling
- **Two frontends** — local CLI (`--mode cli`) and Telegram long-polling bot (`--mode telegram` / `both`)
- **Pairing** — phone chats must `/pair CODE` (code printed on the PC); allowlist persisted under `data/`
- **Inbound media** — files / photos / voice from Telegram are saved to `data/inbox/`; voice can be transcribed with Whisper
- **Safety** — `@require_confirmation` for dangerous ops; console Y/N on PC, Telegram ✅/❌ on phone
- **Tools** — ADB, Cursor, Steam/gaming, system, desktop (files/URL/notify/shell)

## Layout

```
main.py
core/                   # agent, confirmation, registry, telegram, pairing, inbound
tools/                  # auto-discovered plugins
scripts/                # autostart helpers (systemd / Scheduled Task)
data/                   # pair code, allowlist, inbox (gitignored)
requirements.txt
.env.example
```

## Phone + PC model

The orchestrator **always runs on the PC**.

| You are on… | How you talk to it |
|-------------|--------------------|
| PC | `python main.py` |
| Phone | `python main.py --mode telegram` on the PC, then `/pair CODE` in Telegram |
| Both | `python main.py --mode both` |

### Pairing

1. Start Telegram mode on the PC — console prints `Telegram pair code: ……`
2. In Telegram, send `/pair AB12CD`
3. Chat is stored in `data/allowed_chats.json`

Optional: set `TELEGRAM_CHAT_ID` / `TELEGRAM_ALLOWED_CHAT_IDS` or a fixed `TELEGRAM_PAIR_CODE` in `.env`.

### Inbound from phone

Send a document, photo, audio, or voice note:

- Saved under `data/inbox/<chat_id>/<date>/…`
- Voice/audio transcribed with OpenAI Whisper when `OPENAI_API_KEY` is set
- Caption or transcript is forwarded to the agent (with the saved path attached)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Set OPENAI_API_KEY and TELEGRAM_BOT_TOKEN
```

| Variable | Required | Purpose |
|----------|----------|---------|
| `OPENAI_API_KEY` | for chat / whisper | OpenAI |
| `OPENAI_MODEL` | no | Default `gpt-4o-mini` |
| `TELEGRAM_BOT_TOKEN` | phone | BotFather token |
| `TELEGRAM_CHAT_ID` | no | Pre-seed allowlist + backup target |
| `TELEGRAM_ALLOWED_CHAT_IDS` | no | Extra allowlist ids |
| `TELEGRAM_PAIR_CODE` | no | Fixed pair code |
| `NEXUS_INBOX_DIR` | no | Override inbound folder |
| `CONFIRMATION_TIMEOUT_SECONDS` | no | Default `120` |

## Run

```bash
python main.py                      # PC console
python main.py --mode telegram      # phone bot
python main.py --mode both          # both
python main.py -c "CPU usage?"
python main.py --list-tools
```

Telegram commands: `/start` `/help` `/pair CODE` `/tools` `/reset` `/status` `/inbox`

## Autostart (PC always listening for the phone)

**Linux (systemd user):**

```bash
chmod +x scripts/install-autostart-linux.sh
./scripts/install-autostart-linux.sh
# logs: journalctl --user -u nexus-node.service -f
```

**Windows (Scheduled Task at logon):**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-autostart-windows.ps1
```

## Confirmation

Dangerous tools (ADB push, scaffold, steamcmd, `run_shell`, process kill, …) ask before running:

- **CLI** — `Proceed? [y/N]`
- **Telegram** — ✅ / ❌ (timeout = deny)

## Adding a tool

1. Add a function under `tools/` with `@tool` (+ `@require_confirmation` if dangerous).
2. Type annotations + docstring with `Args:`.
3. Restart — discovery is automatic.

## License

MIT-style for personal use; bring your own API keys and device access.
