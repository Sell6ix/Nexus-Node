# Nexus Node

Personal PC assistant orchestrator: natural-language commands → LLM function calling → Python tools.

Works from **the PC console** and from a **phone via Telegram** (same agent, same tools, same confirmation gates).

## Features

- **Plugin tools** under `tools/` — auto-discovered, Pydantic v2 schemas, OpenAI function calling
- **Two frontends** — local CLI (`--mode cli`) and Telegram long-polling bot (`--mode telegram` / `both`)
- **Owner-locked Telegram** — strangers get `Unauthorized`; `/pair` rate-limited; pairing auto-locks after first owner; dangerous ✅/❌ only in the **owner** chat
- **Inbound media** — files / photos / voice saved to `data/inbox/` (size-capped); voice via Whisper
- **Safety** — `@require_confirmation`, shell deny/allowlist, inbound limits, JSONL audit log
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

### Security model (important)

**Anyone who can find your bot username can message it — but that must not mean they control your PC.**

| Layer | Behavior |
|-------|----------|
| Stranger DM | Reply `Unauthorized.` + notify owner; no pair instructions |
| `/pair CODE` | 10-char secret from PC console; 5 fails → 15 min lockout |
| After first pair | Pairing **locks**; code rotates |
| Owner | `TELEGRAM_OWNER_CHAT_ID` (recommended) or first successful `/pair` |
| Dangerous tools | ✅/❌ buttons go **only to the owner chat** (attacker cannot self-approve) |
| `/audit` `/lock` `/unlock` `/revoke` | Owner only |

Recommended `.env`:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_OWNER_CHAT_ID=123456789   # from /whoami
TELEGRAM_CHAT_ID=123456789
SHELL_ENABLED=true
```

Keep the bot username private. Prefer BotFather settings that limit group adds. Do not share pair codes from logs.

### Pairing

1. Start Telegram mode — console prints a **secret** pair code
2. From **your** phone: `/whoami` then `/pair CODE`
3. Put `TELEGRAM_OWNER_CHAT_ID=<your id>` into `.env` and restart
4. Pairing stays locked; use owner `/unlock` only to add another trusted chat

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

Telegram commands: `/start` `/help` `/pair CODE` `/tools` `/reset` `/status` `/inbox` `/audit`

## Safety & audit

- Dangerous tools ask for confirmation (CLI Y/N or Telegram ✅/❌).
- `run_shell` is blocked by default deny patterns (`rm -rf /`, `mkfs`, fork bombs, `shutdown`, piped `curl|sh`, …) even after confirmation.
- Optional `SHELL_ALLOW_PREFIXES` (comma-separated) restricts shell to an allowlist.
- Set `SHELL_ENABLED=false` to disable shell entirely.
- Inbound Telegram files larger than `INBOUND_MAX_BYTES` (default 25 MiB) are rejected.
- Audit trail: `data/audit.jsonl` (or `NEXUS_AUDIT_LOG`). View via Telegram `/audit` or `tail -f data/audit.jsonl`.

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
