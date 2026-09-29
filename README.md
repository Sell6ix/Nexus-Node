# Nexus Node

Personal PC assistant orchestrator: natural-language commands → LLM function calling → Python tools.

## Features

- **Plugin tools** under `tools/` — auto-discovered, Pydantic v2 schemas, OpenAI function calling
- **Safety** — `@require_confirmation` for dangerous ops (file push, process kill, steamcmd, scaffolding); console Y/N with an optional Telegram button stub
- **Tools** — ADB, Cursor projects, Steam/gaming, system stats / process control / Telegram backup

## Layout

```
main.py                 # CLI agent loop
core/
  agent.py              # OpenAI tool-calling loop
  confirmation.py       # @require_confirmation + console/Telegram stub
  config.py             # env-based settings
  registry.py           # @tool marker + auto-discovery
tools/
  adb_tools.py
  cursor_tools.py
  gaming_tools.py
  system_tools.py
requirements.txt
.env.example
```

## Requirements

- Python 3.11+
- An OpenAI API key for the interactive agent (tool listing works without one)

Optional binaries depending on which tools you use: `adb`, `cursor`, `steamcmd`, Steam client.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set OPENAI_API_KEY
```

Environment variables:

| Variable | Required | Purpose |
|----------|----------|---------|
| `OPENAI_API_KEY` | for chat | OpenAI API access |
| `OPENAI_MODEL` | no | Default `gpt-4o-mini` |
| `TELEGRAM_BOT_TOKEN` | no | Telegram Bot API token for backups |
| `TELEGRAM_CHAT_ID` | no | Destination chat for backups |
| `ADB_PATH` | no | Override `adb` binary |
| `CURSOR_CLI_PATH` | no | Override `cursor` CLI |
| `STEAMCMD_PATH` | no | Override `steamcmd` |
| `STEAM_USERNAME` / `STEAM_PASSWORD` | no | Non-anonymous steamcmd login |

If Telegram secrets are missing, `send_telegram_backup` returns a **mock/local fallback** instead of failing hard.

## Run

```bash
# Interactive REPL
python main.py

# Single command
python main.py -c "What's my CPU and memory usage?"

# Inspect plugins (no API key needed)
python main.py --list-tools
python main.py --dump-schemas
```

## Confirmation

Tools decorated with `@require_confirmation` prompt in the console:

```
⚠️  Confirmation required
   Allow tool 'adb_push_file' with arguments: ...
   Proceed? [y/N]:
```

`ask_telegram_confirmation()` is reserved as an optional inline-button hook (stub returning `None` → console fallback).

Process kill via `manage_process(..., action="kill")` always asks before terminating.

## Adding a tool

1. Create or edit a module under `tools/`.
2. Decorate the function with `@tool` (and `@require_confirmation` if dangerous).
3. Use type annotations + an LLM-oriented docstring (`Args:` section helps schemas).
4. Restart the CLI — discovery is automatic.

## License

MIT-style for personal use; bring your own API keys and device access.
