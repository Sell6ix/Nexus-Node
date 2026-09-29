#!/usr/bin/env python3
"""Nexus Node — multi-device daily assistant.

Modes:
  cli       — local console
  telegram  — Telegram hub (phone control)
  both      — CLI + Telegram
  worker    — expose local tools to a hub over HTTP
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
from core.devices import DeviceRegistry
from core.pairing import PairingStore
from core.permissions import Role
from core.registry import ToolRegistry
from core.telegram_api import TelegramClient
from core.telegram_bot import TelegramBot
from core.users import UserStore
from core.worker import WorkerServer


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


def run_repl(agent: Agent) -> int:
    settings = agent.settings
    print("Nexus Node — daily assistant")
    print(f"Device: {settings.device_id} ({settings.device_name})")
    print(f"Model: {settings.openai_model}")
    print(f"OpenAI/.env key: {'yes' if settings.has_openai else 'no (users can /apikey in Telegram)'}")
    print(
        "Telegram: "
        + ("bot ready" if settings.has_telegram_bot else "not configured")
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
                "  <text> — agent\n"
                "  tools / reset / exit\n"
                "Phone: python main.py --mode telegram\n"
                "Other PC worker: python main.py --mode worker\n"
            )
            continue
        if lowered == "tools":
            print_tools(agent.registry)
            continue
        if lowered == "reset":
            agent.reset()
            print("Conversation reset.\n")
            continue
        print(f"\nassistant> {agent.run(user_input)}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nexus Node multi-device assistant")
    parser.add_argument("-c", "--command", help="Run one command and exit")
    parser.add_argument(
        "--mode",
        choices=("cli", "telegram", "both", "worker"),
        default="cli",
    )
    parser.add_argument("--list-tools", action="store_true")
    parser.add_argument("--dump-schemas", action="store_true")
    args = parser.parse_args(argv)

    load_dotenv_if_present()
    registry = build_registry()

    if args.list_tools:
        print_tools(registry)
        return 0
    if args.dump_schemas:
        print(json.dumps(registry.openai_tools(), indent=2))
        return 0

    settings = load_settings()
    devices = DeviceRegistry(
        settings.devices_path,
        local_id=settings.device_id,
        local_name=settings.device_name,
        local_token=settings.worker_token,
    )
    users = UserStore(settings.users_dir)
    audit = get_audit_logger(settings.audit_log_path)

    if args.mode == "worker":
        if not settings.worker_token:
            print(
                "WORKER_TOKEN (or DEVICE_TOKEN) is required for --mode worker.\n"
                "Generate a long random string and put the same value on the hub via /device_add.",
                file=sys.stderr,
            )
            return 2
        worker = WorkerServer(
            registry,
            token=settings.worker_token,
            host=settings.worker_host,
            port=settings.worker_port,
            device_id=settings.device_id,
            device_name=settings.device_name,
        )
        try:
            worker.serve_forever()
        except KeyboardInterrupt:
            print("\nBye.")
            worker.stop()
        return 0

    if args.mode in {"telegram", "both"} and not settings.has_telegram_bot:
        print(
            "TELEGRAM_BOT_TOKEN is required for --mode telegram/both.",
            file=sys.stderr,
        )
        return 2

    shared_gateways: dict[str, TelegramGateway] = {}
    tg_client: TelegramClient | None = None
    pairing: PairingStore | None = None

    if args.mode in {"telegram", "both"}:
        tg_client = TelegramClient(settings.telegram_bot_token)  # type: ignore[arg-type]
        pairing = PairingStore(
            data_dir=settings.data_dir,
            env_pair_code=settings.telegram_pair_code,
            seed_chat_ids=settings.allowed_telegram_chat_ids,
            owner_chat_id=settings.owner_chat_id,
            pairing_open=settings.telegram_pairing_open,
            max_pair_attempts=settings.pair_max_attempts,
            pair_lockout_seconds=settings.pair_lockout_seconds,
            max_friends=settings.max_friends,
        )
        print(f"Device: {settings.device_id} ({settings.device_name})")
        print(f"Telegram pair code (secret): {pairing.pair_code}")
        print(f"Owner chat: {pairing.owner_chat_id or '(none yet)'}")
        if settings.telegram_chat_id:
            shared_gateways[str(settings.telegram_chat_id)] = TelegramGateway(
                client=tg_client,
                chat_id=settings.telegram_chat_id,
                timeout_seconds=settings.confirmation_timeout_seconds,
            )

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

    bot: TelegramBot | None = None
    if args.mode in {"telegram", "both"}:
        bot = TelegramBot(
            settings,
            registry,
            client=tg_client,
            shared_gateways=shared_gateways,
            pairing=pairing,
            users=users,
            devices=devices,
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

    agent = Agent(
        registry,
        settings,
        audit=audit,
        source="cli",
        role=Role.OWNER,
        devices=devices,
        active_device_id=settings.device_id,
    )

    if args.command:
        print(agent.run(args.command))
        return 0

    return run_repl(agent)


if __name__ == "__main__":
    raise SystemExit(main())
