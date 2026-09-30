"""Daily briefing tools: on-demand + morning/evening schedule."""

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
def get_daily_briefing(slot: str = "morning") -> dict[str, Any]:
    """Build a morning or evening-style briefing for the current user.

    Combines weather (Tel Aviv by default), open todos, reminders, local calendar,
    and (if connected) Google Calendar + unread Gmail.

    Args:
        slot: ``morning`` or ``evening`` — changes the summary tone.

    Returns:
        Structured briefing sections the assistant should summarize conversationally.
    """
    try:
        uid = get_user_id()
        cfg = _schedule_store().get(uid)
        loc = str(cfg.get("location") or "").strip() or "Tel Aviv"
        kind = "evening" if str(slot).lower().startswith("eve") else "morning"
        sections = build_briefing(uid, location=loc)
        return {
            "ok": True,
            "briefing": sections,
            "text": format_briefing_text(sections, slot=kind),  # type: ignore[arg-type]
            "schedule": cfg,
            "hint": (
                "Summarize warmly and briefly for voice: weather, what matters, "
                "what's overdue, unread mail. Evening: wind-down + one thing for tomorrow."
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
    evening_enabled: bool = False,
    evening_hour: int = 20,
    evening_minute: int = 0,
) -> dict[str, Any]:
    """Enable or change automatic morning/evening briefing pushes (local clock).

    Args:
        enabled: Morning briefing on/off.
        hour: Morning local hour (0–23).
        minute: Morning minute.
        timezone_name: IANA timezone (default Asia/Jerusalem).
        location: City for weather (default Tel Aviv).
        evening_enabled: Evening briefing on/off.
        evening_hour: Evening local hour (default 20).
        evening_minute: Evening minute.

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
            evening_enabled=bool(evening_enabled),
            evening_hour=int(evening_hour),
            evening_minute=int(evening_minute),
        )
        return {"ok": True, "schedule": cfg}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def get_briefing_schedule() -> dict[str, Any]:
    """Return the user's morning + evening briefing schedule."""
    try:
        cfg = _schedule_store().get(get_user_id())
        return {"ok": True, "schedule": cfg}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
