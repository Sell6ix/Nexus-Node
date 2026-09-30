"""Pytest loads the smoke suites plus a few extra unit checks."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smoke_p0 import (  # noqa: E402, F401
    test_daily_isolation,
    test_device_add_parse,
    test_manage_process_confirm_flag,
    test_worker_confirmed_exec,
)
from smoke_p1 import (  # noqa: E402, F401
    test_home_device,
    test_parse_when,
    test_reminder_delivery,
    test_reminder_retries_if_undelivered,
)
from smoke_secrets import (  # noqa: E402, F401
    test_account_encrypt,
    test_secret_box,
    test_user_store_encrypt,
)
from core.chat_history import ChatHistory
from core.contacts import ContactStore
from core.memory import MemoryStore
from core.tls import ensure_dev_cert
from tools.desktop_tools import _windows_toast_script, _xml_escape
from tools.web_tools import _html_to_text


def test_windows_toast_escapes_xml() -> None:
    script = _windows_toast_script('Hi <Nexus>', 'it\'s "ok" & fine')
    assert "&lt;" in script
    assert "&amp;" in script
    assert "ToastNotification" in script
    assert "<Nexus>" not in script
    assert _xml_escape("&") == "&amp;"


def test_dev_tls_cert(tmp_path: Path) -> None:
    cert, key = ensure_dev_cert(tmp_path)
    assert cert.is_file() and key.is_file()
    text = cert.read_text(encoding="utf-8")
    assert "BEGIN CERTIFICATE" in text
    # reuse existing files
    cert2, key2 = ensure_dev_cert(tmp_path)
    assert cert2 == cert and key2 == key


def test_chat_history_persist(tmp_path: Path) -> None:
    store = ChatHistory(tmp_path / "chats", max_messages=50)
    assert store.load("u1") == []
    store.append("u1", "user", "hello")
    store.append("u1", "assistant", "hi there")
    store.append("u1", "system", "reminder ping")
    rows = store.load("u1")
    assert [r["role"] for r in rows] == ["user", "assistant", "system"]
    assert rows[0]["text"] == "hello"
    turns = ChatHistory.llm_turns(rows)
    assert turns == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    store.clear("u1")
    assert store.load("u1") == []


def test_contacts_store(tmp_path: Path) -> None:
    store = ContactStore(tmp_path / "contacts")
    c = store.upsert("u1", name="Masha", email="masha@example.com", telegram="@masha")
    assert c["id"]
    assert store.find("u1", "masha")["email"] == "masha@example.com"
    assert store.find("u1", "masha@example.com") is not None
    assert store.remove("u1", c["id"])
    assert store.list("u1") == []


def test_html_to_text_strips_tags() -> None:
    text = _html_to_text("<html><head><title>Hi</title><script>x</script></head><body><p>Hello <b>world</b></p></body></html>")
    assert "Hello" in text and "world" in text
    assert "<" not in text
    assert "script" not in text.lower() or "x" not in text


def test_memory_store(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    f = store.add("u1", "Prefers short answers")
    assert f["id"]
    assert "short answers" in store.prompt_block("u1")
    assert store.search("u1", "short")[0]["id"] == f["id"]
    assert store.remove("u1", f["id"]) is not None
    assert store.list("u1") == []


def test_calendar_and_diary_tools(monkeypatch, tmp_path: Path) -> None:
    import tools.calendar_tools as cal
    import tools.daily_tools as daily
    from core.context import request_context

    monkeypatch.setattr(cal, "_ROOT", tmp_path / "daily")
    monkeypatch.setattr(daily, "_ROOT", tmp_path / "daily")

    with request_context(user_id="u-cal", device_id="local"):
        ev = cal.add_calendar_event("Dentist", "tomorrow 15:00", duration_minutes=30)
        assert ev.get("ok"), ev
        listed = cal.list_calendar_events(days=7)
        assert listed.get("ok") and listed.get("count") >= 1
        diary = daily.add_diary_entry("Good day", day="today")
        assert diary.get("ok"), diary
        day = daily.get_diary_day("today")
        assert day.get("count") >= 1
