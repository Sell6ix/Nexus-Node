"""Reminder due-time parsing and Telegram delivery loop."""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


_RELATIVE_RE = re.compile(
    r"^\s*in\s+(\d+)\s*(minutes?|mins?|m|hours?|hrs?|h|days?|d)\s*$",
    re.I,
)
_TIME_ONLY_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")
_DATE_TIME_RE = re.compile(
    r"^\s*(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?\s*$"
)


def parse_when(when: str, *, now: datetime | None = None) -> datetime | None:
    """Parse a due time into an aware UTC datetime, or None if unparsable."""
    text = (when or "").strip()
    if not text:
        return None
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)

    # ISO / fromisoformat (handle trailing Z)
    iso = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass

    m = _RELATIVE_RE.match(text)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith("m"):
            return base + timedelta(minutes=amount)
        if unit.startswith("h"):
            return base + timedelta(hours=amount)
        return base + timedelta(days=amount)

    m = _DATE_TIME_RE.match(text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour = int(m.group(4) or 9)
        minute = int(m.group(5) or 0)
        second = int(m.group(6) or 0)
        return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)

    m = _TIME_ONLY_RE.match(text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        candidate = base.astimezone(timezone.utc).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate <= base:
            candidate += timedelta(days=1)
        return candidate

    # "tomorrow HH:MM" / "tomorrow"
    low = text.lower()
    if low.startswith("tomorrow"):
        rest = text[len("tomorrow") :].strip()
        day = (base + timedelta(days=1)).astimezone(timezone.utc)
        if not rest:
            return day.replace(hour=9, minute=0, second=0, microsecond=0)
        tm = _TIME_ONLY_RE.match(rest)
        if tm:
            return day.replace(
                hour=int(tm.group(1)),
                minute=int(tm.group(2)),
                second=0,
                microsecond=0,
            )

    return None


def load_reminders(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return list(raw) if isinstance(raw, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_reminders(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def due_reminders(
    daily_root: Path,
    *,
    now: datetime | None = None,
    include_app_open: bool = False,
) -> list[tuple[str, Path, dict[str, Any]]]:
    """Return (user_id, reminders_path, reminder) for due undelivered items."""
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    users_dir = Path(daily_root) / "users"
    if not users_dir.is_dir():
        return []
    out: list[tuple[str, Path, dict[str, Any]]] = []
    for user_path in users_dir.iterdir():
        if not user_path.is_dir():
            continue
        rem_path = user_path / "reminders.json"
        rows = load_reminders(rem_path)
        for item in rows:
            if item.get("done") or item.get("notified_at"):
                continue
            deliver = str(item.get("deliver") or "time").lower()
            if deliver == "app_open":
                if include_app_open:
                    out.append((user_path.name, rem_path, item))
                continue
            due: datetime | None = None
            if item.get("due_at"):
                try:
                    due = datetime.fromisoformat(str(item["due_at"]).replace("Z", "+00:00"))
                    if due.tzinfo is None:
                        due = due.replace(tzinfo=timezone.utc)
                    else:
                        due = due.astimezone(timezone.utc)
                except ValueError:
                    due = None
            if due is None and item.get("when"):
                # Absolute forms only — relative "in N minutes" needs due_at at create time.
                due = parse_when(str(item["when"]), now=base)
                if due is not None and _RELATIVE_RE.match(str(item["when"]).strip()):
                    due = None
            if due is not None and due <= base:
                out.append((user_path.name, rem_path, item))
    return out


SendFn = Callable[[str, str], bool | None]


def mark_notified(path: Path, reminder_id: str, *, now: datetime | None = None) -> bool:
    rows = load_reminders(path)
    stamp_dt = now or datetime.now(timezone.utc)
    stamp = stamp_dt.isoformat()
    found = False
    for item in rows:
        if item.get("id") == reminder_id:
            if str(item.get("recur") or "").lower() == "daily":
                # Roll forward ~24h from previous due (or now).
                nxt: datetime | None = None
                if item.get("due_at"):
                    try:
                        prev = datetime.fromisoformat(
                            str(item["due_at"]).replace("Z", "+00:00")
                        )
                        if prev.tzinfo is None:
                            prev = prev.replace(tzinfo=timezone.utc)
                        nxt = prev + timedelta(days=1)
                        while nxt <= stamp_dt:
                            nxt += timedelta(days=1)
                    except ValueError:
                        nxt = None
                if nxt is None:
                    nxt = parse_when(str(item.get("when") or "09:00"), now=stamp_dt + timedelta(minutes=1))
                item["due_at"] = nxt.isoformat() if nxt else None
                item["notified_at"] = None
                item["done"] = False
                item["last_notified_at"] = stamp
            else:
                item["notified_at"] = stamp
                item["done"] = True
            found = True
            break
    if found:
        save_reminders(path, rows)
    return found


def flush_app_open_reminders(
    daily_root: Path,
    user_id: str,
    send: SendFn,
) -> int:
    """Deliver reminders queued for the next app open for one user."""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(user_id))
    path = Path(daily_root) / "users" / (safe or "local") / "reminders.json"
    if not path.is_file():
        return 0
    delivered = 0
    for uid, rem_path, item in due_reminders(daily_root, include_app_open=True):
        if uid != safe and uid != str(user_id):
            continue
        text = str(item.get("text") or "(reminder)")
        body = f"Reminder (on open)\n{text}"
        try:
            ok = send(str(user_id), body)
            if ok is False:
                continue
            mark_notified(rem_path, str(item.get("id")))
            delivered += 1
        except Exception:  # noqa: BLE001
            continue
    return delivered


class ReminderNotifier:
    """Background poller: due reminders → user notification callback."""

    def __init__(
        self,
        *,
        daily_root: Path,
        send: SendFn,
        interval_seconds: float = 30.0,
    ) -> None:
        self.daily_root = Path(daily_root)
        self.send = send
        self.interval_seconds = max(5.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> threading.Thread:
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="reminder-notifier", daemon=True
        )
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()

    def tick_once(self) -> int:
        """Deliver due reminders; return count sent. Safe for tests."""
        delivered = 0
        for user_id, path, item in due_reminders(self.daily_root):
            if user_id in {"local", ""}:
                continue
            text = str(item.get("text") or "(reminder)")
            when = item.get("when") or item.get("due_at") or ""
            body = f"⏰ Reminder\n{text}"
            if when:
                body += f"\n(when: {when})"
            try:
                ok = self.send(user_id, body)
                if ok is False:
                    continue
                mark_notified(path, str(item.get("id")))
                delivered += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[reminders] failed for {user_id}: {exc}")
        return delivered

    def _loop(self) -> None:
        print(f"Reminder notifier every {int(self.interval_seconds)}s…")
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception as exc:  # noqa: BLE001
                print(f"[reminders] tick error: {exc}")
            self._stop.wait(self.interval_seconds)
