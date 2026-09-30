"""Daily briefing tools: on-demand + schedule morning push."""

from __future__ import annotations

from typing import Any

from core.briefing import BriefingScheduleStore, build_briefing, format_briefing_text
from core.context import get_user_id
from core.registry import tool
from pathlib import Path


def _schedule_store() -> BriefingScheduleStore:
    root = Path(__file__).resolve().parent.parent / "data" / "briefing"
    return BriefingScheduleStore(root)


@tool
def get_daily_briefing() -> dict[str, Any]:
    """Build a morning/evening-style briefing for the current user.

    Combines open todos, due reminders, local calendar, and (if connected)
    Google Calendar + unread Gmail count/snippets.

    Returns:
        Structured briefing sections the assistant should summarize conversationally.
    """
    try:
        uid = get_user_id()
        sections = build_briefing(uid)
        return {
            "ok": True,
            "briefing": sections,
            "text": format_briefing_text(sections),
            "hint": (
                "Summarize warmly and briefly for voice: what matters today, "
                "what's overdue, and any unread mail. Offer to help with the top item."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def schedule_daily_briefing(
    enabled: bool = True,
    hour_utc: int = 8,
    minute: int = 0,
) -> dict[str, Any]:
    """Enable or change the automatic daily briefing push (UTC clock).

    When enabled, Nexus pushes a briefing over WebSocket / Web Push around the
    configured UTC time each day (also appears in the General chat as a system line).

    Args:
        enabled: Turn the schedule on or off.
        hour_utc: Hour in UTC (0–23). Convert from local time when talking to the user.
        minute: Minute (0–59).

    Returns:
        Saved schedule.
    """
    try:
        cfg = _schedule_store().set(
            get_user_id(),
            enabled=bool(enabled),
            hour=int(hour_utc),
            minute=int(minute),
        )
        return {"ok": True, "schedule": cfg, "timezone": "UTC"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def get_briefing_schedule() -> dict[str, Any]:
    """Return the user's automatic daily briefing schedule (UTC)."""
    try:
        return {"ok": True, "schedule": _schedule_store().get(get_user_id()), "timezone": "UTC"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
