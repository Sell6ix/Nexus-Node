"""Daily briefing: todos, reminders, local + Google calendar, mail peek."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.context import get_user_id
from core.google_oauth import GoogleClient, GoogleOAuthError, GoogleTokenStore, google_configured
from core.registry import tool
from core.secrets import default_box
import tools.calendar_tools as cal
import tools.daily_tools as daily


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
        sections: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "user_id": uid,
        }

        todos = daily.list_todos(include_done=False)
        sections["todos"] = {
            "count": todos.get("count", 0),
            "items": (todos.get("todos") or [])[:12],
        }

        rems = daily.list_reminders(include_done=False)
        sections["reminders"] = {
            "count": rems.get("count", 0),
            "items": (rems.get("reminders") or [])[:12],
        }

        local_cal = cal.list_calendar_events(days=2)
        sections["local_calendar"] = {
            "count": local_cal.get("count", 0),
            "events": (local_cal.get("events") or [])[:12],
        }

        diary = daily.get_diary_day("today")
        sections["diary_today"] = {
            "count": diary.get("count", 0),
            "entries": (diary.get("entries") or [])[:5],
        }

        google: dict[str, Any] = {"connected": False}
        if google_configured():
            root = Path(__file__).resolve().parent.parent / "data"
            store = GoogleTokenStore(root / "google", default_box(root))
            st = store.status(uid)
            google["connected"] = bool(st.get("connected"))
            google["email"] = st.get("email")
            if st.get("connected"):
                try:
                    client = GoogleClient(store, uid)
                    events = client.list_events(days=2)
                    google["calendar"] = {"count": len(events), "events": events[:12]}
                    mail = client.list_messages(max_results=5, query="is:unread")
                    google["gmail_unread"] = {"count": len(mail), "messages": mail}
                except GoogleOAuthError as exc:
                    google["error"] = str(exc)
        sections["google"] = google

        sections["hint"] = (
            "Summarize warmly and briefly for voice: what matters today, "
            "what's overdue, and any unread mail. Offer to help with the top item."
        )
        return {"ok": True, "briefing": sections}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
