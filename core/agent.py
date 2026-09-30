"""OpenAI-powered agent loop with BYOK, roles, and multi-device tool routing."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from openai import OpenAI

from core.audit import AuditLogger, get_audit_logger
from core.config import Settings, load_settings
from core.confirmation import (
    ConfirmationDenied,
    confirmation_message_for,
    confirm_action,
    needs_confirmation,
    tool_requires_confirmation,
    uses_telegram_button_hook,
)
from core.context import request_context
from core.devices import Device, DeviceRegistry, execute_remote_tool
from core.memory import MemoryStore
from core.permissions import PermissionPolicy, Role, check_tool_permission
from core.registry import ToolRegistry
from core.safety import check_shell_command
from core.users import UserProfile, redact_secrets


SYSTEM_PROMPT = """\
You are Nexus Node, a personal assistant for daily tasks on the user's devices.
Users talk from the web app (phone/PC); legacy Telegram CLI also exists.
Each person can use their own AI API key.

Capabilities:
- Memory: remember lasting facts (preferences, people, routines). Use remember/recall_memory.
- Notes = short sticky items; Diary = dated journal (add_diary_entry / list_diary).
- Calendar: local hub calendar (add_calendar_event / list_calendar_events).
- Todos & reminders on this hub.
- Internet: web_search then fetch_url. Do not invent live data.
- Messaging: contacts + send_email / send_telegram_message (always needs Allow).

Be concise and honest about failures. Never invent tool results.
Prefer short replies that read well on a phone.
If a tool is unavailable for this user's role or device, say so briefly.
"""

AUDITED_TOOLS = frozenset(
    {
        "run_shell",
        "manage_process",
        "kill_process",
        "adb_push_file",
        "create_project_structure",
        "update_game_via_steamcmd",
        "send_telegram_backup",
        "open_url",
        "notify_desktop",
        "launch_steam_game",
        "web_search",
        "fetch_url",
        "send_email",
        "send_telegram_message",
        "add_contact",
        "remove_contact",
    }
)

# Always execute on the hub (SMTP/search/memory live here), even if a remote PC is active.
HUB_LOCAL_TOOLS = frozenset(
    {
        "web_search",
        "fetch_url",
        "list_contacts",
        "add_contact",
        "remove_contact",
        "send_email",
        "send_telegram_message",
        "remember",
        "list_memory",
        "recall_memory",
        "forget_memory",
        "add_diary_entry",
        "list_diary",
        "get_diary_day",
        "add_calendar_event",
        "list_calendar_events",
        "cancel_calendar_event",
        "add_note",
        "list_notes",
        "add_todo",
        "list_todos",
        "complete_todo",
        "set_reminder",
        "list_reminders",
    }
)


class Agent:
    """Conversational tool-calling agent."""

    def __init__(
        self,
        registry: ToolRegistry,
        settings: Settings | None = None,
        *,
        client: OpenAI | None = None,
        confirm_fn: Callable[[str, bool], bool] | None = None,
        audit: AuditLogger | None = None,
        source: str = "cli",
        role: Role = Role.OWNER,
        requester_id: str | None = None,
        user_profile: UserProfile | None = None,
        devices: DeviceRegistry | None = None,
        active_device_id: str | None = None,
    ) -> None:
        self.registry = registry
        self.settings = settings or load_settings()
        self.confirm_fn = confirm_fn or (
            lambda prompt, use_telegram: confirm_action(
                prompt, use_telegram_stub=use_telegram
            )
        )
        self.audit = audit or get_audit_logger(self.settings.audit_log_path)
        self.source = source
        self.role = role
        self.requester_id = requester_id or source
        self.permissions: PermissionPolicy = self.settings.permissions
        self.user_profile = user_profile
        self.devices = devices
        self.active_device_id = active_device_id

        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_content()},
        ]

        if client is not None:
            self.client = client
        else:
            self.client = self._build_client()

    def _system_content(self) -> str:
        role_note = f"\nActive role: {self.role.value}."
        if self.role == Role.FRIEND:
            role_note += " Friend operators have a restricted tool set."
        device = self._resolve_device()
        if device:
            role_note += f"\nActive device: {device.id} ({device.name})."
        block = SYSTEM_PROMPT + role_note
        try:
            memory = MemoryStore(self.settings.data_dir / "memory").prompt_block(
                str(self.requester_id)
            )
            if memory:
                block += "\n\n" + memory
        except Exception:  # noqa: BLE001
            pass
        return block

    def _refresh_system_prompt(self) -> None:
        if self._messages and self._messages[0].get("role") == "system":
            self._messages[0] = {"role": "system", "content": self._system_content()}

    def _build_client(self) -> OpenAI | None:
        api_key = None
        base_url = self.settings.openai_base_url
        if self.user_profile and self.user_profile.api_key:
            api_key = self.user_profile.api_key
            if self.user_profile.api_base_url:
                base_url = self.user_profile.api_base_url
        elif self.settings.openai_api_key:
            api_key = self.settings.openai_api_key
        if not api_key:
            return None
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAI(**kwargs)

    def _model(self) -> str:
        if self.user_profile and self.user_profile.model:
            return self.user_profile.model
        return self.settings.openai_model

    def _resolve_device(self) -> Device | None:
        if self.devices is None:
            return None
        return self.devices.get(self.active_device_id or self.devices.local_id)

    @property
    def ready(self) -> bool:
        return self.client is not None and len(self.registry) > 0

    def reset(self) -> None:
        self._messages = [{"role": "system", "content": self._system_content()}]

    def seed_from_transcript(self, turns: list[dict[str, str]]) -> None:
        """Restore recent user/assistant turns (no tool calls) into the LLM context."""
        self.reset()
        for turn in turns:
            role = turn.get("role")
            content = (turn.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                self._messages.append({"role": role, "content": content})

    def run(self, user_command: str, *, max_tool_rounds: int = 8) -> str:
        self._refresh_system_prompt()
        if self.client is None:
            return (
                "No AI API key configured for this user.\n"
                "• Web app: Settings → paste your AI key (optional model / base URL).\n"
                "• Telegram (legacy): /apikey YOUR_KEY (optional /baseurl /model).\n"
                "• Or set OPENAI_API_KEY in the host .env and restart."
            )

        device = self._resolve_device()
        with request_context(
            user_id=str(self.requester_id),
            device_id=device.id if device else self.active_device_id,
        ):
            return self._run_inner(user_command, max_tool_rounds=max_tool_rounds)

    def _run_inner(self, user_command: str, *, max_tool_rounds: int = 8) -> str:
        self._messages.append({"role": "user", "content": user_command})
        tools = self._tools_for_role()
        self.audit.log(
            "user_command",
            source=self.source,
            role=self.role.value,
            requester=self.requester_id,
            device=(self._resolve_device().id if self._resolve_device() else None),
            text=redact_secrets(_clip(user_command, 500)),
        )

        try:
            for _ in range(max_tool_rounds):
                response = self.client.chat.completions.create(
                    model=self._model(),
                    messages=self._messages,
                    tools=tools or None,
                    tool_choice="auto" if tools else None,
                )
                message = response.choices[0].message
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
                    return (message.content or "").strip() or "(No response.)"

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

            return "Stopped after the maximum number of tool rounds."
        except Exception as exc:  # noqa: BLE001
            self.audit.log(
                "agent_error",
                source=self.source,
                error=redact_secrets(str(exc)),
            )
            return f"Agent error: {redact_secrets(str(exc))}"

    def _tools_for_role(self) -> list[dict[str, Any]]:
        all_tools = self.registry.openai_tools()
        if self.role == Role.OWNER:
            return all_tools
        allowed_names = set(self.permissions.friend_allow_tools) - set(
            self.permissions.friend_deny_tools
        )
        return [
            t
            for t in all_tools
            if t.get("function", {}).get("name") in allowed_names
        ]

    def _execute_tool_call(self, name: str, raw_arguments: str) -> str:
        registered = self.registry.get(name)
        if registered is None:
            return json.dumps({"ok": False, "error": f"Unknown tool: {name}"})

        try:
            arguments = json.loads(raw_arguments) if raw_arguments.strip() else {}
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be a JSON object.")
            validated = registered.parse_arguments(arguments)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": f"Invalid arguments for '{name}': {exc}"})

        perm = check_tool_permission(
            role=self.role,
            tool_name=name,
            arguments=validated,
            policy=self.permissions,
        )
        if not perm.get("ok"):
            self.audit.log(
                "permission_denied",
                source=self.source,
                role=self.role.value,
                requester=self.requester_id,
                tool=name,
                error=perm.get("error"),
            )
            return json.dumps({"ok": False, "error": perm.get("error"), "tool": name})

        requires_confirm = tool_requires_confirmation(name, registered.func, validated)
        should_audit = name in AUDITED_TOOLS or requires_confirm
        if should_audit:
            self.audit.log(
                "tool_call",
                source=self.source,
                role=self.role.value,
                requester=self.requester_id,
                tool=name,
                args=_safe_args(validated),
            )

        if name == "run_shell":
            gate = check_shell_command(str(validated.get("command") or ""), self.settings.safety)
            if not gate.get("ok"):
                self.audit.log(
                    "safety_blocked",
                    source=self.source,
                    tool=name,
                    error=gate.get("error"),
                )
                return json.dumps({"ok": False, "error": gate.get("error"), "tool": name})

        func = registered.func
        device = self._resolve_device()
        remote = (
            device is not None
            and not device.is_local
            and name not in HUB_LOCAL_TOOLS
        )

        if requires_confirm:
            prompt = confirmation_message_for(func, validated, tool_name=name)
            detailed = (
                f"{prompt}\n\n"
                f"Requested by: {self.requester_id} (role={self.role.value})\n"
                f"Device: {device.id if device else 'local'}"
                f"{' (remote)' if remote else ''}\n"
                f"Tool: {name}\nArgs: {json.dumps(validated, default=str)}"
            )
            allowed = self.confirm_fn(
                detailed,
                uses_telegram_button_hook(func) if needs_confirmation(func) else True,
            )
            if not allowed:
                self.audit.log(
                    "confirmation_denied",
                    source=self.source,
                    tool=name,
                    args=_safe_args(validated),
                )
                return json.dumps(
                    {"ok": False, "error": "User denied confirmation.", "tool": name}
                )
            try:
                if remote:
                    # Hub already confirmed — execute on the selected worker.
                    result = execute_remote_tool(
                        device, name, validated, confirmed=True  # type: ignore[arg-type]
                    )
                else:
                    # Unwrap @require_confirmation to avoid a second local prompt.
                    target = (
                        getattr(func, "__wrapped__", func)
                        if needs_confirmation(func)
                        else func
                    )
                    result = target(**validated)
            except Exception as exc:  # noqa: BLE001
                self.audit.log("tool_error", source=self.source, tool=name, error=str(exc))
                return json.dumps({"ok": False, "error": str(exc), "tool": name})
        else:
            try:
                if remote:
                    result = execute_remote_tool(device, name, validated)  # type: ignore[arg-type]
                else:
                    result = func(**validated)
            except ConfirmationDenied as exc:
                self.audit.log(
                    "confirmation_denied",
                    source=self.source,
                    tool=name,
                    error=str(exc),
                )
                return json.dumps({"ok": False, "error": str(exc), "tool": name})
            except Exception as exc:  # noqa: BLE001
                if should_audit:
                    self.audit.log("tool_error", source=self.source, tool=name, error=str(exc))
                return json.dumps({"ok": False, "error": str(exc), "tool": name})

        serialized = _serialize_result(result)
        if should_audit:
            ok = True
            try:
                parsed = json.loads(serialized)
                if isinstance(parsed, dict) and parsed.get("ok") is False:
                    ok = False
            except json.JSONDecodeError:
                pass
            self.audit.log(
                "tool_result",
                source=self.source,
                tool=name,
                ok=ok,
                result=_clip(redact_secrets(serialized), 800),
            )
        return serialized


def _serialize_result(result: Any) -> str:
    if isinstance(result, (dict, list)):
        return json.dumps(result, default=str)
    dump = getattr(result, "model_dump", None)
    if callable(dump):
        return json.dumps(dump(), default=str)
    return json.dumps({"ok": True, "result": result}, default=str)


def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in args.items():
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        out[key] = _clip(redact_secrets(str(text)), 300)
    return out


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
