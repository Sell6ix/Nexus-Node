"""Gmail + Google Calendar tools (requires OAuth connect in Settings)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from core.confirmation import require_confirmation
from core.context import get_user_id
from core.google_oauth import GoogleClient, GoogleOAuthError, GoogleTokenStore, google_configured
from core.registry import tool
from core.reminders import parse_when
from core.secrets import default_box


def _client() -> GoogleClient:
    root = Path(__file__).resolve().parent.parent / "data"
    store = GoogleTokenStore(root / "google", default_box(root))
    return GoogleClient(store, get_user_id())


@tool
def google_status() -> dict[str, Any]:
    """Check whether Google (Gmail/Calendar) is connected for this user.

    Returns:
        Connection status and account email when linked.
    """
    try:
        root = Path(__file__).resolve().parent.parent / "data"
        store = GoogleTokenStore(root / "google", default_box(root))
        st = store.status(get_user_id())
        return {"ok": True, **st}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def gmail_list(max_results: int = 8, query: str = "is:unread") -> dict[str, Any]:
    """List Gmail messages (default: unread). Requires Google connect.

    Args:
        max_results: 1–20 messages.
        query: Gmail search query (default is:unread).

    Returns:
        Message metadata (from, subject, snippet).
    """
    try:
        if not google_configured():
            return {
                "ok": False,
                "error": "Google OAuth not configured on hub (.env GOOGLE_CLIENT_ID/SECRET).",
            }
        rows = _client().list_messages(max_results=max_results, query=query)
        return {"ok": True, "count": len(rows), "messages": rows, "query": query}
    except GoogleOAuthError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
@require_confirmation("Send email via the user's connected Gmail account.")
def gmail_send(to: str, subject: str, body: str) -> dict[str, Any]:
    """Send email through connected Gmail. Requires confirmation.

    Args:
        to: Recipient email.
        subject: Subject line.
        body: Plain text body.

    Returns:
        Gmail API message id on success.
    """
    try:
        result = _client().send_email(to=to.strip(), subject=subject.strip(), body=body)
        return {"ok": True, "id": result.get("id"), "to": to.strip()}
    except GoogleOAuthError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def google_calendar_list(days: int = 7) -> dict[str, Any]:
    """List upcoming Google Calendar events.

    Args:
        days: Look-ahead window (1–60).

    Returns:
        Events from the primary calendar.
    """
    try:
        rows = _client().list_events(days=days)
        return {"ok": True, "count": len(rows), "events": rows}
    except GoogleOAuthError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
@require_confirmation("Create an event on the user's Google Calendar.")
def google_calendar_add(
    title: str,
    when: str,
    duration_minutes: int = 60,
    location: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Create a Google Calendar event. Requires confirmation.

    Args:
        title: Event title.
        when: Start (ISO / tomorrow 15:00 / in 2 hours…).
        duration_minutes: Length in minutes.
        location: Optional location.
        notes: Optional description.

    Returns:
        Created event id and link.
    """
    try:
        start = parse_when(when)
        if start is None:
            return {"ok": False, "error": "Could not parse when."}
        mins = max(5, min(int(duration_minutes or 60), 60 * 24 * 14))
        end = start + timedelta(minutes=mins)
        result = _client().create_event(
            title=title.strip(),
            start_iso=start.isoformat(),
            end_iso=end.isoformat(),
            location=location.strip(),
            description=notes.strip(),
        )
        return {
            "ok": True,
            "id": result.get("id"),
            "htmlLink": result.get("htmlLink"),
            "title": title.strip(),
            "start": start.isoformat(),
        }
    except GoogleOAuthError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
