"""Shared daily briefing builder + per-user morning/evening schedule."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.context import request_context
from core.google_oauth import GoogleClient, GoogleOAuthError, GoogleTokenStore, google_configured
from core.secrets import default_box
from core.weather import weather_for_city
import tools.calendar_tools as cal
import tools.daily_tools as daily

Slot = Literal["morning", "evening"]


def _tz(name: str | None) -> ZoneInfo:
    raw = (name or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(raw)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def build_briefing(user_id: str, *, location: str | None = None) -> dict[str, Any]:
    """Collect briefing sections for ``user_id`` (sets request context)."""
    with request_context(user_id=user_id, device_id="hub"):
        sections: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id,
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
            st = store.status(user_id)
            google["connected"] = bool(st.get("connected"))
            google["email"] = st.get("email")
            if st.get("connected"):
                try:
                    client = GoogleClient(store, user_id)
                    events = client.list_events(days=2)
                    google["calendar"] = {"count": len(events), "events": events[:12]}
                    mail = client.list_messages(max_results=5, query="is:unread")
                    google["gmail_unread"] = {"count": len(mail), "messages": mail}
                except GoogleOAuthError as exc:
                    google["error"] = str(exc)
        sections["google"] = google

        city = (location or "").strip()
        if city:
            sections["weather"] = weather_for_city(city)
        else:
            sections["weather"] = {"ok": False, "skipped": True}
        try:
            import tools.travel_tools as travel

            nxt = travel.whats_next(buffer_minutes=10)
            if nxt.get("ok"):
                sections["whats_next"] = {
                    "next": nxt.get("next"),
                    "leave_by_utc": nxt.get("leave_by_utc"),
                    "travel_minutes": nxt.get("travel_minutes"),
                    "route": {
                        "ok": (nxt.get("route") or {}).get("ok"),
                        "eta": (nxt.get("route") or {}).get("eta"),
                        "links": (nxt.get("route") or {}).get("links"),
                    }
                    if nxt.get("route")
                    else None,
                }
        except Exception as exc:  # noqa: BLE001
            sections["whats_next"] = {"ok": False, "error": str(exc)}
        return sections


def format_briefing_text(sections: dict[str, Any], *, slot: Slot = "morning") -> str:
    """Short plain-text briefing for push / chat."""
    title = "Evening briefing" if slot == "evening" else "Morning briefing"
    lines = [title]
    wx = sections.get("weather") or {}
    if wx.get("ok"):
        place = (wx.get("place") or {}).get("name") or "there"
        temp = wx.get("temperature_c")
        summary = wx.get("summary") or ""
        hi = wx.get("today_high_c")
        lo = wx.get("today_low_c")
        rain = wx.get("precip_chance_pct")
        bit = f"Weather in {place}: {temp}°C, {summary}"
        if hi is not None and lo is not None:
            bit += f" (high {hi}° / low {lo}°)"
        if rain is not None:
            bit += f", rain {rain}%"
        lines.append(bit)

    todos = sections.get("todos") or {}
    if todos.get("count"):
        label = "Still open" if slot == "evening" else f"Todos ({todos['count']})"
        lines.append(f"{label}:")
        for t in (todos.get("items") or [])[:5]:
            lines.append(f"• {t.get('title') or t}")
    else:
        lines.append("Todos: none open")

    events = (sections.get("local_calendar") or {}).get("events") or []
    gcal = ((sections.get("google") or {}).get("calendar") or {}).get("events") or []
    all_ev = list(events) + list(gcal)
    if all_ev:
        lines.append("Calendar:" if slot == "morning" else "Coming up:")
        for ev in all_ev[:6]:
            title_ev = ev.get("title") or ev.get("summary") or "event"
            when = ev.get("start_at") or ev.get("start") or ""
            lines.append(f"• {title_ev} ({when})")
    else:
        lines.append("Calendar: clear")

    mail = (sections.get("google") or {}).get("gmail_unread") or {}
    if mail.get("count"):
        lines.append(f"Unread mail: {mail['count']}")
        for m in (mail.get("messages") or [])[:3]:
            lines.append(f"• {m.get('subject') or '(no subject)'} — {m.get('from') or ''}")
    elif (sections.get("google") or {}).get("connected"):
        lines.append("Unread mail: 0")

    rems = sections.get("reminders") or {}
    if rems.get("count"):
        lines.append(f"Reminders open: {rems['count']}")

    nxt = (sections.get("whats_next") or {}).get("next") or {}
    if nxt.get("title"):
        leave = (sections.get("whats_next") or {}).get("leave_by_utc")
        bit = f"Next: {nxt.get('title')} at {nxt.get('start') or nxt.get('start_utc')}"
        if leave:
            bit += f" — leave by {leave}"
        lines.append(bit)

    if slot == "evening":
        lines.append("Wind down — pick one thing for tomorrow if you want.")
    return "\n".join(lines)


class BriefingScheduleStore:
    """``data/briefing/<user>.json`` — morning + optional evening, IANA tz."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, user_id: str) -> Path:
        safe = re.sub(r"[^\w\-]", "_", str(user_id)) or "user"
        return self.root / f"{safe}.json"

    def get(self, user_id: str) -> dict[str, Any]:
        path = self._path(user_id)
        defaults = {
            "enabled": False,
            "hour": 8,
            "minute": 0,
            "timezone": "Asia/Jerusalem",
            "location": "Tel Aviv",
            "last_sent_day": None,
            "evening_enabled": False,
            "evening_hour": 20,
            "evening_minute": 0,
            "evening_last_sent_day": None,
        }
        if not path.is_file():
            return dict(defaults)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return dict(defaults)
        tz_name = str(raw.get("timezone") or "Asia/Jerusalem").strip() or "Asia/Jerusalem"
        _ = _tz(tz_name)
        return {
            "enabled": bool(raw.get("enabled")),
            "hour": int(raw.get("hour") if raw.get("hour") is not None else 8),
            "minute": int(raw.get("minute") if raw.get("minute") is not None else 0),
            "timezone": tz_name,
            "location": str(raw.get("location") or "Tel Aviv").strip() or "Tel Aviv",
            "last_sent_day": raw.get("last_sent_day"),
            "evening_enabled": bool(raw.get("evening_enabled")),
            "evening_hour": int(
                raw.get("evening_hour") if raw.get("evening_hour") is not None else 20
            ),
            "evening_minute": int(
                raw.get("evening_minute") if raw.get("evening_minute") is not None else 0
            ),
            "evening_last_sent_day": raw.get("evening_last_sent_day"),
        }

    def set(
        self,
        user_id: str,
        *,
        enabled: bool | None = None,
        hour: int | None = None,
        minute: int | None = None,
        timezone_name: str | None = None,
        location: str | None = None,
        evening_enabled: bool | None = None,
        evening_hour: int | None = None,
        evening_minute: int | None = None,
    ) -> dict[str, Any]:
        cur = self.get(user_id)
        tz_name = (
            timezone_name if timezone_name is not None else cur.get("timezone")
        ) or "Asia/Jerusalem"
        tz_name = str(tz_name).strip() or "Asia/Jerusalem"
        try:
            ZoneInfo(tz_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone '{tz_name}'") from exc
        loc = cur.get("location") or "Tel Aviv"
        if location is not None:
            loc = str(location).strip() or "Tel Aviv"
        if enabled is not None:
            cur["enabled"] = bool(enabled)
        if hour is not None:
            cur["hour"] = max(0, min(23, int(hour)))
        if minute is not None:
            cur["minute"] = max(0, min(59, int(minute)))
        if evening_enabled is not None:
            cur["evening_enabled"] = bool(evening_enabled)
        if evening_hour is not None:
            cur["evening_hour"] = max(0, min(23, int(evening_hour)))
        if evening_minute is not None:
            cur["evening_minute"] = max(0, min(59, int(evening_minute)))
        cur["timezone"] = tz_name
        cur["location"] = loc
        self._path(user_id).write_text(json.dumps(cur, indent=2) + "\n", encoding="utf-8")
        return cur

    def mark_sent(self, user_id: str, day: str, *, slot: Slot = "morning") -> None:
        cur = self.get(user_id)
        if slot == "evening":
            cur["evening_last_sent_day"] = day
        else:
            cur["last_sent_day"] = day
        self._path(user_id).write_text(json.dumps(cur, indent=2) + "\n", encoding="utf-8")

    def due_slots(self, *, now: datetime | None = None) -> list[tuple[str, Slot]]:
        """Return ``(user_id, slot)`` pairs that are due now."""
        base_utc = now or datetime.now(timezone.utc)
        if base_utc.tzinfo is None:
            base_utc = base_utc.replace(tzinfo=timezone.utc)
        out: list[tuple[str, Slot]] = []
        for path in self.root.glob("*.json"):
            uid = path.stem
            cfg = self.get(uid)
            local = base_utc.astimezone(_tz(str(cfg.get("timezone") or "Asia/Jerusalem")))
            day = local.date().isoformat()
            hm = (local.hour, local.minute)
            if cfg.get("enabled") and cfg.get("last_sent_day") != day:
                if hm >= (int(cfg["hour"]), int(cfg["minute"])):
                    out.append((uid, "morning"))
            if cfg.get("evening_enabled") and cfg.get("evening_last_sent_day") != day:
                if hm >= (int(cfg["evening_hour"]), int(cfg["evening_minute"])):
                    out.append((uid, "evening"))
        return out

    def due_users(self, *, now: datetime | None = None) -> list[str]:
        """Backward-compat: users with any due morning slot."""
        seen: list[str] = []
        for uid, slot in self.due_slots(now=now):
            if slot == "morning" and uid not in seen:
                seen.append(uid)
        return seen


SendFn = Callable[[str, str, str], bool | None]


class BriefingNotifier:
    """Push scheduled briefings over the same channel as reminders."""

    def __init__(
        self,
        store: BriefingScheduleStore,
        *,
        send: SendFn,
        interval_seconds: float = 60.0,
    ) -> None:
        self.store = store
        self.send = send
        self.interval_seconds = max(30.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="briefing-notifier", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def tick_once(self) -> int:
        sent = 0
        base_utc = datetime.now(timezone.utc)
        for uid, slot in self.store.due_slots(now=base_utc):
            try:
                cfg = self.store.get(uid)
                local = base_utc.astimezone(
                    _tz(str(cfg.get("timezone") or "Asia/Jerusalem"))
                )
                day = local.date().isoformat()
                loc = str(cfg.get("location") or "").strip() or "Tel Aviv"
                sections = build_briefing(uid, location=loc)
                text = format_briefing_text(sections, slot=slot)
                title = "Evening briefing" if slot == "evening" else "Morning briefing"
                ok = self.send(uid, text, title)
                if ok is False:
                    continue
                self.store.mark_sent(uid, day, slot=slot)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[briefing] failed for {uid}/{slot}: {exc}")
        return sent

    def _loop(self) -> None:
        print(f"Briefing notifier every {int(self.interval_seconds)}s…")
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception as exc:  # noqa: BLE001
                print(f"[briefing] tick error: {exc}")
            self._stop.wait(self.interval_seconds)
