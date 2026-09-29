# Nexus Node

Personal PC assistant orchestrator: natural-language commands → LLM function calling → Python tools.

Works from **the PC console** and from a **phone via Telegram** (same agent, same tools, same confirmation gates).

## Features

- **Plugin tools** under `tools/` — auto-discovered, Pydantic v2 schemas, OpenAI function calling
- **Two frontends** — local CLI (`--mode cli`) and Telegram long-polling bot (`--mode telegram` / `both`)
- **Safety** — `@require_confirmation` for dangerous ops; console Y/N on PC, Telegram ✅/❌ buttons on phone
- **Tools** — ADB, Cursor projects, Steam/gaming, system stats / process control / Telegram file pull

## Layout

```
main.py                 # CLI + Telegram entrypoint
core/
  agent.py              # OpenAI tool-calling loop
  confirmation.py       # @require_confirmation + pluggable gateways
  config.py             # env-based settings
  registry.py           # @tool marker + auto-discovery
  telegram_api.py       # thin Bot API client (stdlib)
  telegram_bot.py       # phone interface (long polling)
tools/
  adb_tools.py
  cursor_tools.py
  gaming_tools.py
  system_tools.py
requirements.txt
.env.example
```

## How phone + PC fit together

The orchestrator **always runs on the PC** (it needs local access to processes, Steam, ADB, files).

| You are on… | How you talk to it |
|-------------|--------------------|
| PC | `python main.py` (console REPL) |
| Phone | `python main.py --mode telegram` on the PC, then chat with the bot in Telegram |
| Both | `python main.py --mode both` — CLI locally, phone in parallel; dangerous ops can confirm via Telegram buttons |

## Requirements

- Python 3.11+
- `OPENAI_API_KEY` for the agent
- For phone access: a Telegram bot token from [@BotFather](https://t.me/BotFather)

Optional binaries: `adb`, `cursor`, `steamcmd`, Steam client.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env — at minimum OPENAI_API_KEY
# For phone: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (or TELEGRAM_ALLOWED_CHAT_IDS)
```

### Environment

| Variable | Required | Purpose |
|----------|----------|---------|
| `OPENAI_API_KEY` | for chat | OpenAI API access |
| `OPENAI_MODEL` | no | Default `gpt-4o-mini` |
| `TELEGRAM_BOT_TOKEN` | phone / backup | Bot API token |
| `TELEGRAM_CHAT_ID` | recommended | Your chat id (backup target + allowlist) |
| `TELEGRAM_ALLOWED_CHAT_IDS` | no | Comma-separated allowlist (extra chats) |
| `CONFIRMATION_TIMEOUT_SECONDS` | no | Default `120` for Telegram Yes/No |
| `ADB_PATH` / `CURSOR_CLI_PATH` / `STEAMCMD_PATH` | no | Binary overrides |
| `STEAM_USERNAME` / `STEAM_PASSWORD` | no | Non-anonymous steamcmd |

If Telegram secrets are missing, `send_telegram_backup` returns a **mock/local fallback**.

**Finding your chat id:** start the bot with an open allowlist once, send `/status`, or use a helper like `@userinfobot`. Then put the id in `.env`.

## Run

```bash
# PC console
python main.py

# Phone remote control (run on the PC)
python main.py --mode telegram

# CLI + Telegram together
python main.py --mode both

# Single command
python main.py -c "What's my CPU and memory usage?"

# Inspect plugins (no API key needed)
python main.py --list-tools
python main.py --dump-schemas
```

Telegram slash commands: `/start` `/help` `/tools` `/reset` `/status`.

## Confirmation

Dangerous tools (ADB push, project scaffold, steamcmd update, process kill) ask before running:

- **CLI** — `Proceed? [y/N]`
- **Telegram** — inline ✅ Yes / ❌ No (timeout denies)

## Adding a tool

1. Create or edit a module under `tools/`.
2. Decorate with `@tool` (and `@require_confirmation` if dangerous).
3. Use type annotations + an LLM-oriented docstring (`Args:` helps schemas).
4. Restart — discovery is automatic.

## License

MIT-style for personal use; bring your own API keys and device access.
