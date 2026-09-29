"""OpenAI-powered agent loop: command → tool selection → confirm → execute → reply."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from openai import OpenAI

from core.config import Settings, load_settings
from core.confirmation import (
    ConfirmationDenied,
    confirmation_message_for,
    confirm_action,
    needs_confirmation,
    uses_telegram_button_hook,
)
from core.registry import ToolRegistry


SYSTEM_PROMPT = """\
You are Nexus Node, a personal PC assistant orchestrator.
You help the user by selecting and calling the provided Python tools.
Be concise, practical, and honest about failures or missing dependencies.
When a tool result is enough to answer, summarize it clearly for the user.
Never invent tool results — only report what tools actually returned.
"""


class Agent:
    """Conversational tool-calling agent backed by the OpenAI Chat Completions API."""

    def __init__(
        self,
        registry: ToolRegistry,
        settings: Settings | None = None,
        *,
        client: OpenAI | None = None,
        confirm_fn: Callable[[str, bool], bool] | None = None,
    ) -> None:
        self.registry = registry
        self.settings = settings or load_settings()
        self.confirm_fn = confirm_fn or (
            lambda prompt, use_telegram: confirm_action(
                prompt, use_telegram_stub=use_telegram
            )
        )
        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        if client is not None:
            self.client = client
        elif self.settings.has_openai:
            self.client = OpenAI(api_key=self.settings.openai_api_key)
        else:
            self.client = None

    @property
    def ready(self) -> bool:
        return self.client is not None and len(self.registry) > 0

    def reset(self) -> None:
        """Clear conversation history (keeps the system prompt)."""
        self._messages = [self._messages[0]]

    def run(self, user_command: str, *, max_tool_rounds: int = 8) -> str:
        """Process one user command through the tool-calling loop.

        Returns the final assistant reply text.
        """
        if self.client is None:
            return (
                "OPENAI_API_KEY is not set. Export it (or place it in a .env file) "
                "and restart. Tip: run `python main.py --list-tools` to inspect "
                "available tools without an API key."
            )

        self._messages.append({"role": "user", "content": user_command})
        tools = self.registry.openai_tools()

        for _ in range(max_tool_rounds):
            response = self.client.chat.completions.create(
                model=self.settings.openai_model,
                messages=self._messages,
                tools=tools or None,
                tool_choice="auto" if tools else None,
            )
            choice = response.choices[0]
            message = choice.message

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": message.content or "",
            }
            if message.tool_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments or "{}",
                        },
                    }
                    for tc in message.tool_calls
                ]
            self._messages.append(assistant_message)

            if not message.tool_calls:
                reply = (message.content or "").strip() or "(No response.)"
                return reply

            for tool_call in message.tool_calls:
                result_text = self._execute_tool_call(
                    tool_call.function.name,
                    tool_call.function.arguments or "{}",
                )
                self._messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result_text,
                    }
                )

        return "Stopped after the maximum number of tool rounds. Please try a narrower request."

    def _execute_tool_call(self, name: str, raw_arguments: str) -> str:
        registered = self.registry.get(name)
        if registered is None:
            return json.dumps({"ok": False, "error": f"Unknown tool: {name}"})

        try:
            arguments = json.loads(raw_arguments) if raw_arguments.strip() else {}
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be a JSON object.")
            validated = registered.parse_arguments(arguments)
        except Exception as exc:  # noqa: BLE001 — surface validation errors to the LLM
            return json.dumps(
                {
                    "ok": False,
                    "error": f"Invalid arguments for '{name}': {exc}",
                }
            )

        func = registered.func
        # The decorator wrapper already confirms on direct call, but the agent
        # asks once up-front so the user sees intent before any side effects,
        # then invokes the underlying function without double-prompting when
        # possible. We still call through the public wrapper for safety.
        if needs_confirmation(func):
            prompt = confirmation_message_for(func, validated)
            allowed = self.confirm_fn(prompt, uses_telegram_button_hook(func))
            if not allowed:
                return json.dumps(
                    {
                        "ok": False,
                        "error": "User denied confirmation.",
                        "tool": name,
                    }
                )
            # Bypass the wrapper's second prompt by calling __wrapped__ when present.
            target = getattr(func, "__wrapped__", func)
            try:
                result = target(**validated)
            except Exception as exc:  # noqa: BLE001
                return json.dumps({"ok": False, "error": str(exc), "tool": name})
        else:
            try:
                result = func(**validated)
            except ConfirmationDenied as exc:
                return json.dumps({"ok": False, "error": str(exc), "tool": name})
            except Exception as exc:  # noqa: BLE001
                return json.dumps({"ok": False, "error": str(exc), "tool": name})

        return _serialize_result(result)


def _serialize_result(result: Any) -> str:
    if isinstance(result, (dict, list)):
        return json.dumps(result, default=str)
    dump = getattr(result, "model_dump", None)
    if callable(dump):
        return json.dumps(dump(), default=str)
    return json.dumps({"ok": True, "result": result}, default=str)
