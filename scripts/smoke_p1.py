#!/usr/bin/env python3
"""Smoke checks for P1: reminders + home device."""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.context import request_context
from core.reminders import ReminderNotifier, due_reminders, parse_when
from core.users import UserStore
from tools import daily_tools


def test_parse_when() -> None:
    now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    assert parse_when("in 10 minutes", now=now) == now + timedelta(minutes=10)
    assert parse_when("in 2 hours", now=now) == now + timedelta(hours=2)
    assert parse_when("2026-09-30 09:30", now=now).hour == 9  # type: ignore[union-attr]
    assert parse_when("tomorrow 08:00", now=now).day == 30  # type: ignore[union-attr]
    assert parse_when("not a time", now=now) is None
    print("OK parse_when")


def test_reminder_delivery() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        daily_tools._ROOT = root  # type: ignore[attr-defined]
        sent: list[tuple[str, str]] = []

        with request_context(user_id="12345"):
            due = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
            r = daily_tools.set_reminder("buy milk", due)
            assert r["ok"] and r["reminder"]["due_at"]

        notifier = ReminderNotifier(daily_root=root, send=lambda c, t: sent.append((c, t)) or True)
        n = notifier.tick_once()
        assert n == 1, (n, sent)
        assert sent[0][0] == "12345"
        assert "buy milk" in sent[0][1]
        # Second tick should not re-send
        assert notifier.tick_once() == 0
    print("OK reminder delivery")


def test_reminder_retries_if_undelivered() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        daily_tools._ROOT = root  # type: ignore[attr-defined]
        with request_context(user_id="999"):
            due = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
            assert daily_tools.set_reminder("ping later", due)["ok"]
        attempts = {"n": 0}

        def fail_once(user_id: str, text: str) -> bool:
            attempts["n"] += 1
            return attempts["n"] > 1

        notifier = ReminderNotifier(daily_root=root, send=fail_once)
        assert notifier.tick_once() == 0
        assert notifier.tick_once() == 1
        assert notifier.tick_once() == 0
    print("OK reminder retry")


def test_home_device() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = UserStore(Path(tmp))
        p = store.ensure_home_device("99", "home-pc")
        assert p.home_device_id == "home-pc"
        store.ensure_home_device("99", "laptop")
        assert store.get("99").home_device_id == "home-pc"  # unchanged
        store.set_home_device("99", "laptop")
        p = store.get("99")
        assert p.resolve_device_id("local") == "laptop"
        store.set_active_device("99", "gaming")
        assert store.get("99").resolve_device_id("local") == "gaming"
        store.set_active_device("99", None)
        assert store.get("99").resolve_device_id("local") == "laptop"
    print("OK home device")


def main() -> int:
    test_parse_when()
    test_reminder_delivery()
    test_reminder_retries_if_undelivered()
    test_home_device()
    print("All P1 smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
