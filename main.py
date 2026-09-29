#!/usr/bin/env python3
"""Nexus Node — personal assistant orchestrator.

Modes:
  cli       — local console on the PC (default)
  telegram  — control the same PC agent from a phone via Telegram
  both      — CLI + Telegram together (confirmations can use phone buttons)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.agent import Agent
from core.audit import get_audit_logger
from core.config import load_settings
from core.confirmation import (
    ConsoleGateway,
    PreferTelegramThenConsoleGateway,
    TelegramGateway,
    set_confirmation_gateway,
)
from core.pairing import PairingStore
from core.registry import ToolRegistry
from core.telegram_api import TelegramClient
from core.telegram_bot import TelegramBot


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.discover("tools")
    return registry


def load_dotenv_if_present() -> None:
    env_path = _ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
    except ImportError:
        pass


def print_tools(registry: ToolRegistry) -> None:
    print(f"Registered tools ({len(registry)}):\n")
    for item in registry.list_tools():
        first_line = item.description.splitlines()[0] if item.description else ""
        print(f"  • {item.name}")
        print(f"      {first_line}")
    print()


def print_schemas(registry: ToolRegistry) -> None:
    print(json.dumps(registry.openai_tools(), indent=2))


def run_repl(agent: Agent) -> int:
    settings = agent.settings
    print("Nexus Node — personal assistant orchestrator")
    print(f"Model: {settings.openai_model}")
    print(f"OpenAI: {'configured' if settings.has_openai else 'MISSING (set OPENAI_API_KEY)'}")
    print(
        "Telegram: "
        + (
            "bot ready"
            if settings.has_telegram_bot
            else "not configured (set TELEGRAM_BOT_TOKEN for phone access)"
        )
    )
    print(f"Tools: {len(agent.registry)}")
    print("Type a command, or 'help' / 'tools' / 'reset' / 'exit'.\n")

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return 0

        if not user_input:
            continue
        lowered = user_input.lower()
        if lowered in {"exit", "quit", "q"}:
            print("Bye.")
            return 0
        if lowered == "help":
            print(
                "Commands:\n"
                "  <text>   — send to the agent\n"
                "  tools    — list registered tools\n"
                "  reset    — clear conversation memory\n"
                "  exit     — quit\n"
                "\n"
                "Phone access: run `python main.py --mode telegram` on this PC,\n"
                "then send /pair CODE from Telegram (code is printed on start).\n"
            )
            continue
        if lowered == "tools":
            print_tools(agent.registry)
            continue
        if lowered == "reset":
            agent.reset()
            print("Conversation reset.\n")
            continue

        reply = agent.run(user_input)
        print(f"\nassistant> {reply}\n")


def _build_telegram_gateway(settings) -> tuple[TelegramClient, dict[str, TelegramGateway]] | None:
    if not settings.has_telegram_bot:
        return None
    client = TelegramClient(settings.telegram_bot_token)
    gateways: dict[str, TelegramGateway] = {}
    if settings.telegram_chat_id:
        gateways[str(settings.telegram_chat_id)] = TelegramGateway(
            client=client,
            chat_id=settings.telegram_chat_id,
            timeout_seconds=settings.confirmation_timeout_seconds,
        )
    return client, gateways


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Nexus Node personal assistant orchestrator (PC CLI + Telegram phone)",
    )
    parser.add_argument(
        "-c",
        "--command",
        help="Run a single natural-language command and exit",
    )
    parser.add_argument(
        "--mode",
        choices=("cli", "telegram", "both"),
        default="cli",
        help="cli=local console, telegram=phone bot, both=CLI+Telegram together",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="List auto-discovered tools and exit",
    )
    parser.add_argument(
        "--dump-schemas",
        action="store_true",
        help="Print OpenAI function-calling schemas as JSON and exit",
    )
    args = parser.parse_args(argv)

    load_dotenv_if_present()
    registry = build_registry()

    if args.list_tools:
        print_tools(registry)
        return 0
    if args.dump_schemas:
        print_schemas(registry)
        return 0

    settings = load_settings()

    if args.mode in {"telegram", "both"}:
        if not settings.has_telegram_bot:
            print(
                "TELEGRAM_BOT_TOKEN is required for --mode telegram/both.\n"
                "Create a bot via @BotFather, put the token in .env, then retry.",
                file=sys.stderr,
            )
            return 2

    shared_gateways: dict[str, TelegramGateway] = {}
    tg_client: TelegramClient | None = None
    if args.mode in {"telegram", "both"}:
        tg_bundle = _build_telegram_gateway(settings)
        if tg_bundle is None:
            print("Failed to initialize Telegram client.", file=sys.stderr)
            return 2
        tg_client, shared_gateways = tg_bundle

    # In CLI/both, prefer Telegram buttons when a chat is configured and bot is running.
    if args.mode == "both" and settings.telegram_chat_id and tg_client is not None:
        primary = shared_gateways.setdefault(
            str(settings.telegram_chat_id),
            TelegramGateway(
                client=tg_client,
                chat_id=settings.telegram_chat_id,
                timeout_seconds=settings.confirmation_timeout_seconds,
            ),
        )
        set_confirmation_gateway(PreferTelegramThenConsoleGateway(telegram=primary))
    elif args.mode == "cli":
        set_confirmation_gateway(ConsoleGateway())

    pairing: PairingStore | None = None
    if args.mode in {"telegram", "both"}:
        pairing = PairingStore(
            data_dir=settings.data_dir,
            env_pair_code=settings.telegram_pair_code,
            seed_chat_ids=settings.allowed_telegram_chat_ids,
            owner_chat_id=settings.owner_chat_id,
            pairing_open=settings.telegram_pairing_open,
            max_pair_attempts=settings.pair_max_attempts,
            pair_lockout_seconds=settings.pair_lockout_seconds,
        )
        print(f"Telegram pair code (secret): {pairing.pair_code}")
        print(f"Owner chat: {pairing.owner_chat_id or '(none yet)'}")
        print(f"Pairing open: {pairing.pairing_open}")
        print(f"Paired chats file: {pairing.allowed_path}")
        print(f"Inbound inbox: {settings.inbox_dir.resolve()}")

    bot: TelegramBot | None = None
    if args.mode in {"telegram", "both"}:
        bot = TelegramBot(
            settings,
            registry,
            client=tg_client,
            shared_gateways=shared_gateways,
            pairing=pairing,
        )

    if args.mode == "telegram":
        assert bot is not None
        try:
            bot.run_forever()
        except KeyboardInterrupt:
            print("\nBye.")
            bot.stop()
        return 0

    if args.mode == "both":
        assert bot is not None
        bot.run_in_background()

    audit = get_audit_logger(settings.audit_log_path)
    agent = Agent(registry, settings, audit=audit, source="cli")

    if args.command:
        print(agent.run(args.command))
        return 0

    return run_repl(agent)


if __name__ == "__main__":
    raise SystemExit(main())
