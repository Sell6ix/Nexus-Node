"""Role-based tool permissions for owner vs friend operators."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Role(str, Enum):
    OWNER = "owner"
    FRIEND = "friend"
    STRANGER = "stranger"


# Tools a friend may call by default (still subject to owner confirmation when marked).
DEFAULT_FRIEND_ALLOW_TOOLS: frozenset[str] = frozenset(
    {
        "get_system_stats",
        "check_game_running",
        "manage_process",  # kill blocked separately
        "list_directory",
        "read_text_file",
        "open_url",
        "notify_desktop",
        "adb_get_device_status",
        "open_project_in_cursor",
        "launch_steam_game",
        "adb_send_notification_or_toast",
        "add_note",
        "list_notes",
        "add_todo",
        "list_todos",
        "complete_todo",
        "set_reminder",
        "set_alarm",
        "list_reminders",
        "web_search",
        "fetch_url",
        "summarize_url",
        "whats_next",
        "plan_route",
        "set_home_location",
        "get_home_location",
        "list_contacts",
        "add_contact",
        "remove_contact",
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
        "sync_calendars",
        "get_daily_briefing",
        "schedule_daily_briefing",
        "get_briefing_schedule",
        "google_status",
        "gmail_list",
        "gmail_send",
        "google_calendar_list",
        "google_calendar_add",
    }
)

# Always denied for friends even if listed in an allow override by mistake.
DEFAULT_FRIEND_DENY_TOOLS: frozenset[str] = frozenset(
    {
        "run_shell",
        "adb_push_file",
        "create_project_structure",
        "update_game_via_steamcmd",
        "send_telegram_backup",
        "send_email",
        "send_telegram_message",
        "send_whatsapp_message",
    }
)


@dataclass(frozen=True)
class PermissionPolicy:
    """Who may call which tools."""

    friend_allow_tools: frozenset[str] = DEFAULT_FRIEND_ALLOW_TOOLS
    friend_deny_tools: frozenset[str] = DEFAULT_FRIEND_DENY_TOOLS
    friend_can_kill_process: bool = False
    notify_owner_on_friend_command: bool = True


def parse_tool_set(raw: str | None, *, default: frozenset[str]) -> frozenset[str]:
    if raw is None or not str(raw).strip():
        return default
    parts = {p.strip() for p in str(raw).replace("\n", ",").split(",") if p.strip()}
    return frozenset(parts) if parts else default


def check_tool_permission(
    *,
    role: Role,
    tool_name: str,
    arguments: dict[str, Any],
    policy: PermissionPolicy,
) -> dict[str, Any]:
    """Return ``{ok: True}`` or a denial payload."""
    if role == Role.OWNER:
        return {"ok": True}
    if role == Role.STRANGER:
        return {"ok": False, "error": "Unauthorized role.", "blocked_by": "role"}

    # Friend
    if tool_name in policy.friend_deny_tools:
        return {
            "ok": False,
            "error": f"Tool '{tool_name}' is owner-only.",
            "blocked_by": "friend_deny",
        }
    if tool_name not in policy.friend_allow_tools:
        return {
            "ok": False,
            "error": f"Tool '{tool_name}' is not allowed for friends.",
            "blocked_by": "friend_allow",
        }
    if tool_name == "kill_process" and not policy.friend_can_kill_process:
        return {
            "ok": False,
            "error": "Friends cannot kill processes (owner-only).",
            "blocked_by": "friend_kill",
        }
    if tool_name == "manage_process":
        action = str(arguments.get("action") or "check").lower()
        if action == "kill" and not policy.friend_can_kill_process:
            return {
                "ok": False,
                "error": "Friends cannot kill processes (owner-only).",
                "blocked_by": "friend_kill",
            }
    return {"ok": True}
