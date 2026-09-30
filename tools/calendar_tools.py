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


@tool
def sync_calendars(direction: str = "both", days: int = 14) -> dict[str, Any]:
    """Sync the hub local calendar with Google Calendar.

    ``pull`` copies Google → local (deduped by google event id).
    ``push`` creates Google events for local rows that are not yet linked
    (asks Allow first). ``both`` does pull then push.

    Args:
        direction: ``pull``, ``push``, or ``both`` (default).
        days: Look-ahead window for Google pull (1–60).

    Returns:
        Counts of pulled / pushed / skipped events.
    """
    from core.confirmation import confirm_action
    from core.google_oauth import (
        GoogleClient,
        GoogleOAuthError,
        GoogleTokenStore,
        google_configured,
    )
    from core.secrets import default_box

    direction_norm = (direction or "both").strip().lower()
    if direction_norm not in {"pull", "push", "both"}:
        return {"ok": False, "error": "direction must be pull, push, or both"}
    if not google_configured():
        return {"ok": False, "error": "Google not configured on hub (.env GOOGLE_CLIENT_*)."}

    root = Path(__file__).resolve().parent.parent / "data"
    store = GoogleTokenStore(root / "google", default_box(root))
    uid = get_user_id()
    st = store.status(uid)
    if not st.get("connected"):
        return {"ok": False, "error": "Connect Google in Settings first."}

    try:
        client = GoogleClient(store, uid)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    pulled = 0
    pushed = 0
    skipped = 0
    errors: list[str] = []

    if direction_norm in {"pull", "both"}:
        try:
            remote = client.list_events(days=max(1, min(int(days or 14), 60)))
        except GoogleOAuthError as exc:
            return {"ok": False, "error": str(exc)}
        rows = _load()
        known = {
            str(ev.get("google_event_id"))
            for ev in rows
            if ev.get("google_event_id")
        }
        for gev in remote:
            gid = str(gev.get("id") or "")
            if not gid or gid in known:
                skipped += 1
                continue
            start = str(gev.get("start") or "")
            end = str(gev.get("end") or "")
            if not start:
                skipped += 1
                continue
            if "T" not in start:
                start_at = f"{start}T00:00:00+00:00"
                end_at = f"{end or start}T23:59:00+00:00"
            else:
                start_at = start.replace("Z", "+00:00")
                end_at = (end or start).replace("Z", "+00:00")
            event = {
                "id": uuid.uuid4().hex[:10],
                "title": (gev.get("title") or "Google event").strip(),
                "start_at": start_at,
                "end_at": end_at,
                "when": start_at,
                "duration_minutes": None,
                "location": gev.get("location"),
                "notes": gev.get("htmlLink"),
                "google_event_id": gid,
                "source": "google",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "cancelled": False,
                "user_id": uid,
            }
            rows.append(event)
            known.add(gid)
            pulled += 1
        _save(rows[-500:])

    if direction_norm in {"push", "both"}:
        if not confirm_action("Push unsynced local calendar events to Google Calendar."):
            return {
                "ok": False,
                "error": "Push cancelled",
                "pulled": pulled,
                "hint": "Allow the confirmation to push local events to Google.",
            }

        rows = _load()
        changed = False
        for ev in rows:
            if ev.get("cancelled") or ev.get("google_event_id"):
                continue
            title = str(ev.get("title") or "").strip()
            start_iso = str(ev.get("start_at") or "")
            end_iso = str(ev.get("end_at") or "")
            if not title or not start_iso:
                skipped += 1
                continue
            try:
                result = client.create_event(
                    title=title,
                    start_iso=start_iso,
                    end_iso=end_iso or start_iso,
                    location=str(ev.get("location") or ""),
                    description=str(ev.get("notes") or ""),
                )
                ev["google_event_id"] = result.get("id")
                ev["source"] = ev.get("source") or "local"
                changed = True
                pushed += 1
            except GoogleOAuthError as exc:
                errors.append(str(exc))
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
        if changed:
            _save(rows)

    return {
        "ok": True,
        "direction": direction_norm,
        "pulled": pulled,
        "pushed": pushed,
        "skipped": skipped,
        "errors": errors[:5],
    }
