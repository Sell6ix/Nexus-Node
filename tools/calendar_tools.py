"""Local personal calendar (hub JSON; no Google OAuth required)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.context import get_user_id
from core.registry import tool
from core.reminders import parse_when

_ROOT = Path(__file__).resolve().parent.parent / "data" / "daily"


def _user_dir() -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in get_user_id())
    path = _ROOT / "users" / (safe or "local")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path() -> Path:
    return _user_dir() / "calendar.json"


def _load() -> list[dict[str, Any]]:
    path = _path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return list(raw) if isinstance(raw, list) else list(raw.get("events") or [])
    except (OSError, json.JSONDecodeError):
        return []


def _save(rows: list[dict[str, Any]]) -> None:
    _path().write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@tool
def add_calendar_event(
    title: str,
    when: str,
    duration_minutes: int = 60,
    location: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Add an event to the user's local calendar on the hub.

    Args:
        title: Event title.
        when: Start time (ISO, 'tomorrow 15:00', 'in 2 hours', 'HH:MM', …).
        duration_minutes: Length in minutes (default 60).
        location: Optional place / link.
        notes: Optional notes.

    Returns:
        The created event (UTC start/end).
    """
    try:
        name = title.strip()
        if not name:
            return {"ok": False, "error": "title must not be empty"}
        start = parse_when(when)
        if start is None:
            return {
                "ok": False,
                "error": "Could not parse when. Try ISO, 'tomorrow 15:00', or 'in 2 hours'.",
            }
        mins = max(5, min(int(duration_minutes or 60), 60 * 24 * 14))
        end = start + timedelta(minutes=mins)
        rows = _load()
        event = {
            "id": uuid.uuid4().hex[:10],
            "title": name,
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "when": when.strip(),
            "duration_minutes": mins,
            "location": location.strip() or None,
            "notes": notes.strip() or None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "cancelled": False,
            "user_id": get_user_id(),
        }
        rows.append(event)
        _save(rows[-500:])
        return {"ok": True, "event": event}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def list_calendar_events(days: int = 14, include_cancelled: bool = False) -> dict[str, Any]:
    """List upcoming calendar events for the current user.

    Args:
        days: Look-ahead window from now (1–90, default 14).
        include_cancelled: Include cancelled events.

    Returns:
        Events sorted by start time.
    """
    try:
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(days=max(1, min(int(days or 14), 90)))
        rows = _load()
        out: list[dict[str, Any]] = []
        for ev in rows:
            if not include_cancelled and ev.get("cancelled"):
                continue
            try:
                start = datetime.fromisoformat(str(ev.get("start_at") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if start < now - timedelta(hours=1):
                continue
            if start > horizon:
                continue
            out.append(ev)
        out.sort(key=lambda e: str(e.get("start_at") or ""))
        return {"ok": True, "count": len(out), "events": out}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def cancel_calendar_event(event_id: str) -> dict[str, Any]:
    """Cancel a calendar event by id.

    Args:
        event_id: Id from list_calendar_events / add_calendar_event.

    Returns:
        Updated event or error.
    """
    try:
        rows = _load()
        for ev in rows:
            if ev.get("id") == event_id.strip():
                ev["cancelled"] = True
                ev["cancelled_at"] = datetime.now(timezone.utc).isoformat()
                _save(rows)
                return {"ok": True, "event": ev}
        return {"ok": False, "error": f"Event not found: {event_id}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
