#!/usr/bin/env python3
"""Nexus Node — personal assistant orchestrator CLI.

Agent loop: natural-language command → LLM tool selection → confirmation
(if required) → execute → reply.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure the repo root is importable when launched as a script.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.agent import Agent
from core.config import load_settings
from core.registry import ToolRegistry


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.discover("tools")
    return registry


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
        f"Telegram: {'configured' if settings.has_telegram else 'mock fallback (no bot token/chat id)'}"
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Nexus Node personal assistant orchestrator",
    )
    parser.add_argument(
        "-c",
        "--command",
        help="Run a single natural-language command and exit",
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

    # Optional .env support without making python-dotenv a hard dependency.
    env_path = _ROOT / ".env"
    if env_path.is_file():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_path)
        except ImportError:
            pass

    registry = build_registry()

    if args.list_tools:
        print_tools(registry)
        return 0
    if args.dump_schemas:
        print_schemas(registry)
        return 0

    settings = load_settings()
    agent = Agent(registry, settings)

    if args.command:
        print(agent.run(args.command))
        return 0

    return run_repl(agent)


if __name__ == "__main__":
    raise SystemExit(main())
