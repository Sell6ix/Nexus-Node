"""Next event, home base, and route/ETA helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.context import get_user_id
from core.prefs import PrefsStore
from core.registry import tool
from core.routing import plan_trip
import tools.calendar_tools as cal


def _prefs() -> PrefsStore:
    root = Path(__file__).resolve().parent.parent / "data" / "prefs"
    return PrefsStore(root)


def _next_events(limit: int = 5) -> list[dict[str, Any]]:
    """Merge local + Google upcoming events, sorted."""
    rows: list[dict[str, Any]] = []
    local = cal.list_calendar_events(days=3)
    for ev in local.get("events") or []:
        rows.append(
            {
                "title": ev.get("title"),
                "start": ev.get("start_at"),
                "end": ev.get("end_at"),
                "location": ev.get("location"),
                "source": "local",
                "id": ev.get("id"),
            }
        )
    try:
        from core.google_oauth import (
            GoogleClient,
            GoogleOAuthError,
            GoogleTokenStore,
            google_configured,
        )
        from core.secrets import default_box

        if google_configured():
            root = Path(__file__).resolve().parent.parent / "data"
            store = GoogleTokenStore(root / "google", default_box(root))
            uid = get_user_id()
            if store.status(uid).get("connected"):
                for gev in GoogleClient(store, uid).list_events(days=3):
                    rows.append(
                        {
                            "title": gev.get("title"),
                            "start": gev.get("start"),
                            "end": gev.get("end"),
                            "location": gev.get("location"),
                            "source": "google",
                            "id": gev.get("id"),
                        }
                    )
    except Exception:  # noqa: BLE001
        pass

    now = datetime.now(timezone.utc)
    dated: list[tuple[datetime, dict[str, Any]]] = []
    for ev in rows:
        raw = str(ev.get("start") or "")
        if not raw:
            continue
        try:
            if "T" not in raw:
                start = datetime.fromisoformat(f"{raw}T00:00:00+00:00")
            else:
                start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            else:
                start = start.astimezone(timezone.utc)
        except ValueError:
            continue
        if start + timedelta(hours=1) < now:
            continue
        dated.append((start, ev))
    dated.sort(key=lambda x: x[0])
    out: list[dict[str, Any]] = []
    for start, ev in dated[: max(1, min(limit, 12))]:
        item = dict(ev)
        item["start_utc"] = start.isoformat()
        item["minutes_until"] = max(0, int((start - now).total_seconds() // 60))
        out.append(item)
    return out


@tool
def set_home_location(address: str) -> dict[str, Any]:
    """Set the user's home / usual start point for route planning.

    Args:
        address: Street or city (e.g. 'Rothschild Blvd, Tel Aviv').

    Returns:
        Saved prefs (geocoded when possible).
    """
    from core.routing import geocode

    try:
        addr = address.strip()
        if not addr:
            return {"ok": False, "error": "address must not be empty"}
        geo = geocode(addr)
        fields: dict[str, Any] = {"home_address": addr}
        if geo:
            fields["home_lat"] = geo["lat"]
            fields["home_lon"] = geo["lon"]
            fields["home_address"] = geo.get("label") or addr
        cfg = _prefs().set(get_user_id(), **fields)
        return {"ok": True, "prefs": cfg, "geocoded": bool(geo)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def get_home_location() -> dict[str, Any]:
    """Return the saved home address used for route ETAs."""
    try:
        return {"ok": True, "prefs": _prefs().get(get_user_id())}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def plan_route(
    destination: str,
    origin: str = "",
    mode: str = "driving",
) -> dict[str, Any]:
    """Plan a route with ETA and Google/Apple Maps links (OpenStreetMap/OSRM).

    Args:
        destination: Where to go (address or place name).
        origin: Start point; empty uses saved home (default Tel Aviv).
        mode: ``driving`` or ``walking``.

    Returns:
        Duration, distance, and deep links. Open the maps link for turn-by-turn.
    """
    try:
        prefs = _prefs().get(get_user_id())
        start = (origin or "").strip() or str(prefs.get("home_address") or "Tel Aviv")
        coords = None
        if not (origin or "").strip() and prefs.get("home_lat") is not None:
            coords = (float(prefs["home_lat"]), float(prefs["home_lon"]))
        trip = plan_trip(start, destination.strip(), mode=mode, origin_coords=coords)
        if not trip.get("ok"):
            return trip
        return {
            **trip,
            "hint": (
                "Tell the user ETA and when to leave. Share the Google Maps link. "
                "This is not live turn-by-turn navigation."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def whats_next(buffer_minutes: int = 10) -> dict[str, Any]:
    """What's the next calendar event, and when should the user leave?

    Combines local + Google calendar. If the event has a location, computes
    driving ETA from home and a suggested leave-by time.

    Args:
        buffer_minutes: Extra minutes before departure (default 10).

    Returns:
        Next event, optional route, leave_by suggestion.
    """
    try:
        events = _next_events(limit=5)
        if not events:
            return {
                "ok": True,
                "next": None,
                "message": "No upcoming events in the next few days.",
            }
        nxt = events[0]
        result: dict[str, Any] = {
            "ok": True,
            "next": nxt,
            "upcoming": events[:5],
        }
        loc = str(nxt.get("location") or "").strip()
        if loc:
            prefs = _prefs().get(get_user_id())
            home = str(prefs.get("home_address") or "Tel Aviv")
            coords = None
            if prefs.get("home_lat") is not None:
                coords = (float(prefs["home_lat"]), float(prefs["home_lon"]))
            trip = plan_trip(home, loc, mode="driving", origin_coords=coords)
            result["route"] = trip
            if trip.get("ok") and trip.get("eta"):
                mins = int(trip["eta"]["duration_minutes"]) + max(0, int(buffer_minutes))
                start = datetime.fromisoformat(str(nxt["start_utc"]))
                leave = start - timedelta(minutes=mins)
                result["leave_by_utc"] = leave.isoformat()
                result["travel_minutes"] = trip["eta"]["duration_minutes"]
                result["buffer_minutes"] = buffer_minutes
                result["hint"] = (
                    f"Suggest leaving by {leave.isoformat()} "
                    f"({mins} min before start including buffer). "
                    f"Offer the maps link and optionally set_alarm / set_reminder."
                )
            elif trip.get("ok"):
                result["hint"] = "Event has a place — share the maps link even without ETA."
        else:
            result["hint"] = (
                "Next event has no location. Offer to add one or ask where they're going."
            )
        return result
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
