"""Daily briefing tools: on-demand + schedule morning push (local timezone)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.briefing import BriefingScheduleStore, build_briefing, format_briefing_text
from core.context import get_user_id
from core.registry import tool


def _schedule_store() -> BriefingScheduleStore:
    root = Path(__file__).resolve().parent.parent / "data" / "briefing"
    return BriefingScheduleStore(root)


@tool
def get_daily_briefing() -> dict[str, Any]:
    """Build a morning/evening-style briefing for the current user.

    Combines weather (if a city is set on the schedule), open todos, due reminders,
    local calendar, and (if connected) Google Calendar + unread Gmail.

    Returns:
        Structured briefing sections the assistant should summarize conversationally.
    """
    try:
        uid = get_user_id()
        cfg = _schedule_store().get(uid)
        loc = str(cfg.get("location") or "").strip() or "Tel Aviv"
        sections = build_briefing(uid, location=loc)
        return {
            "ok": True,
            "briefing": sections,
            "text": format_briefing_text(sections),
            "schedule": {
                "timezone": cfg.get("timezone"),
                "location": cfg.get("location"),
                "enabled": cfg.get("enabled"),
                "hour": cfg.get("hour"),
                "minute": cfg.get("minute"),
            },
            "hint": (
                "Summarize warmly and briefly for voice: weather, what matters today, "
                "what's overdue, and any unread mail. Offer to help with the top item."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def schedule_daily_briefing(
    enabled: bool = True,
    hour: int = 8,
    minute: int = 0,
    timezone_name: str = "Asia/Jerusalem",
    location: str = "Tel Aviv",
) -> dict[str, Any]:
    """Enable or change the automatic daily briefing push (local clock).

    When enabled, Nexus pushes a briefing over WebSocket / Web Push around the
    configured local time each day (also appears in the General chat as a system line).

    Args:
        enabled: Turn the schedule on or off.
        hour: Local hour (0–23) in ``timezone_name``.
        minute: Minute (0–59).
        timezone_name: IANA timezone — default Asia/Jerusalem (Israel).
        location: City for weather (Open-Meteo) — default Tel Aviv.

    Returns:
        Saved schedule.
    """
    try:
        cfg = _schedule_store().set(
            get_user_id(),
            enabled=bool(enabled),
            hour=int(hour),
            minute=int(minute),
            timezone_name=str(timezone_name or "Asia/Jerusalem"),
            location=str(location or "Tel Aviv"),
        )
        return {"ok": True, "schedule": cfg}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def get_briefing_schedule() -> dict[str, Any]:
    """Return the user's automatic daily briefing schedule (local timezone + city)."""
    try:
        cfg = _schedule_store().get(get_user_id())
        return {"ok": True, "schedule": cfg}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
