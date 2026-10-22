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


def test_chat_conversations(tmp_path: Path) -> None:
    store = ChatHistory(tmp_path / "chats", max_messages=50)
    store.append("u1", "user", "general", "main")
    work = store.create_conversation("u1", "Work")
    store.append("u1", "user", "standup notes", work["id"])
    assert store.load("u1", "main")[0]["text"] == "general"
    assert store.load("u1", work["id"])[0]["text"] == "standup notes"
    titles = {c["title"] for c in store.list_conversations("u1")}
    assert "Work" in titles
    assert store.rename_conversation("u1", work["id"], "Office")["title"] == "Office"
    assert store.delete_conversation("u1", work["id"])
    assert work["id"] not in {c["id"] for c in store.list_conversations("u1")}


def test_device_registry_access_control(tmp_path: Path) -> None:
    import pytest

    from core.devices import DeviceRegistry

    store = DeviceRegistry(tmp_path / "devices.json", local_id="hub", local_name="Hub")
    store.upsert(
        "alice-pc",
        name="Alice PC",
        base_url="http://alice",
        token="alice-token",
        owner_chat_id="alice",
    )
    assert store.can_access("hub", user_id="bob", is_owner=False)
    assert store.can_access("alice-pc", user_id="alice", is_owner=False)
    assert not store.can_access("alice-pc", user_id="bob", is_owner=False)
    assert store.can_access("alice-pc", user_id="bob", is_owner=True)
    assert not store.can_manage("hub", user_id="alice", is_owner=True)
    assert not store.can_manage("alice-pc", user_id="bob", is_owner=False)
    assert store.can_manage("alice-pc", user_id="alice", is_owner=False)
    assert store.can_manage("bob-pc", user_id="bob", is_owner=False)
    with pytest.raises(PermissionError):
        store.upsert_for_user(
            "alice-pc",
            name="Hijacked",
            base_url="http://attacker",
            token="attacker-token",
            user_id="bob",
            is_owner=False,
        )
    assert store.get("alice-pc").name == "Alice PC"


def test_account_registration_is_atomic(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from core.accounts import AccountStore

    store = AccountStore(tmp_path / "accounts.json")

    def register_owner(username: str):
        try:
            return store.register(username, "password1")
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        owners = list(pool.map(register_owner, ("owner-one", "owner-two")))
    assert sum(account is not None and account.role == "owner" for account in owners) == 1

    owner = next(account for account in owners if account is not None)
    invite = store.create_invite(owner.id)

    def register_with_invite(username: str) -> bool:
        try:
            store.register(username, "password1", invite_code=invite.code)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        used = list(pool.map(register_with_invite, ("member-one", "member-two")))
    assert sum(used) == 1


def test_upload_read_is_bounded() -> None:
    import asyncio

    import pytest
    from fastapi import HTTPException

    from core.web_hub import _read_limited_upload

    class Upload:
        def __init__(self, data: bytes) -> None:
            self.data = data
            self.requested = 0

        async def read(self, size: int = -1) -> bytes:
            self.requested = size
            return self.data[:size]

    upload = Upload(b"x" * 11)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_read_limited_upload(upload, 10, "too large"))
    assert exc.value.status_code == 413
    assert upload.requested == 11


def test_websocket_ticket_is_single_use(monkeypatch, tmp_path: Path) -> None:
    import pytest
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from core.config import load_settings
    from core.registry import ToolRegistry
    from core.web_hub import create_app

    monkeypatch.setenv("NEXUS_DATA_DIR", str(tmp_path / "data"))
    app = create_app(load_settings(), ToolRegistry())
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/register",
            json={"username": "ws-owner", "password": "password1"},
        )
        assert response.status_code == 200
        token = response.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        ticket_response = client.post("/api/ws/ticket", headers=headers)
        assert ticket_response.status_code == 200
        ticket = ticket_response.json()["ticket"]

        with client.websocket_connect(f"/api/ws?ticket={ticket}") as websocket:
            assert websocket.receive_json()["type"] == "hello"

        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/api/ws?ticket={ticket}") as websocket:
                websocket.receive_json()
        assert exc.value.code == 4401


def test_briefing_schedule(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    from core.briefing import BriefingScheduleStore, format_briefing_text

    store = BriefingScheduleStore(tmp_path / "briefing")
    store.set("u1", enabled=True, hour=8, minute=0, timezone_name="UTC")
    assert store.get("u1")["enabled"] is True
    now = datetime(2026, 3, 15, 8, 5, tzinfo=timezone.utc)
    assert "u1" in store.due_users(now=now)
    store.mark_sent("u1", "2026-03-15")
    assert "u1" not in store.due_users(now=now)

    # Israel (UTC+2 winter): 08:00 Asia/Jerusalem = 06:00 UTC
    store.set(
        "u2",
        enabled=True,
        hour=8,
        minute=0,
        timezone_name="Asia/Jerusalem",
        location="Tel Aviv",
        evening_enabled=True,
        evening_hour=20,
        evening_minute=0,
    )
    early = datetime(2026, 1, 15, 5, 59, tzinfo=timezone.utc)
    assert ("u2", "morning") not in store.due_slots(now=early)
    local_due = datetime(2026, 1, 15, 6, 1, tzinfo=timezone.utc)
    assert ("u2", "morning") in store.due_slots(now=local_due)
    eve_due = datetime(2026, 1, 15, 18, 1, tzinfo=timezone.utc)  # 20:01 Israel
    slots = [s for s in store.due_slots(now=eve_due) if s[0] == "u2"]
    assert ("u2", "morning") in slots
    assert ("u2", "evening") in slots
    store.mark_sent("u2", "2026-01-15", slot="morning")
    store.mark_sent("u2", "2026-01-15", slot="evening")
    assert [s for s in store.due_slots(now=eve_due) if s[0] == "u2"] == []
    assert store.get("u2")["location"] == "Tel Aviv"
    assert store.get("missing")["timezone"] == "Asia/Jerusalem"

    text = format_briefing_text(
        {
            "todos": {"count": 1, "items": [{"title": "Buy milk"}]},
            "local_calendar": {"events": []},
            "reminders": {"count": 0},
            "google": {"connected": False},
            "weather": {
                "ok": True,
                "place": {"name": "Tel Aviv"},
                "temperature_c": 24,
                "summary": "partly cloudy",
                "today_high_c": 27,
                "today_low_c": 18,
                "precip_chance_pct": 10,
            },
        },
        slot="evening",
    )
    assert "Evening briefing" in text
    assert "Buy milk" in text
    assert "Tel Aviv" in text
    assert "partly cloudy" in text


def test_weather_code_label() -> None:
    from core.weather import _code_label

    assert _code_label(0) == "clear"
    assert _code_label(61) == "light rain"


def test_parse_when_jerusalem() -> None:
    from datetime import datetime, timezone

    from core.reminders import parse_when

    now = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)  # Israel winter UTC+2
    due = parse_when("12:00", now=now)
    assert due is not None
    assert due.hour == 10  # 12:00 IST → 10:00 UTC


def test_maps_links() -> None:
    from core.routing import maps_links

    links = maps_links(origin="Tel Aviv", destination="Haifa", mode="driving")
    assert "google.com/maps" in links["google_maps"]
    assert "Haifa" in links["google_maps"]
    assert "maps.apple.com" in links["apple_maps"]


def test_overlay_stats() -> None:
    from core.overlay_stats import collect_overlay_stats

    data = collect_overlay_stats()
    assert data["ok"] is True
    assert "cpu_percent" in data
    assert "memory" in data
    assert isinstance(data.get("top_processes"), list)


def test_set_alarm(tmp_path: Path) -> None:
    from datetime import datetime, timedelta, timezone

    from core.context import request_context
    import tools.daily_tools as daily

    daily._ROOT = tmp_path  # type: ignore[attr-defined]
    with request_context(user_id="u-alarm"):
        due = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        res = daily.set_alarm(due, label="Wake up")
    assert res["ok"]
    assert res["alarm"]["kind"] == "alarm"
    assert "Wake up" in res["alarm"]["text"]


def test_contacts_store(tmp_path: Path) -> None:
    store = ContactStore(tmp_path / "contacts")
    c = store.upsert(
        "u1",
        name="Masha",
        email="masha@example.com",
        telegram="@masha",
        whatsapp="79001234567",
    )
    assert c["id"]
    assert store.find("u1", "masha")["email"] == "masha@example.com"
    assert store.find("u1", "masha@example.com") is not None
    assert store.find("u1", "79001234567")["whatsapp"] == "79001234567"
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


def test_backup_zip(tmp_path: Path) -> None:
    from core.backup import build_backup_zip

    data = tmp_path / "data"
    data.mkdir()
    (data / "chats").mkdir()
    (data / "chats" / "u.json").write_text('{"messages":[]}\n', encoding="utf-8")
    (data / "secrets.key").write_text("x\n", encoding="utf-8")
    blob = build_backup_zip(data, include_secrets_key=False)
    assert blob[:2] == b"PK"
    import zipfile, io

    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = zf.namelist()
        assert any(n.endswith("chats/u.json") for n in names)
        assert not any(n.endswith("secrets.key") for n in names)


def test_upload_store(tmp_path: Path) -> None:
    from core.uploads import UploadStore

    store = UploadStore(tmp_path / "uploads")
    meta = store.save(
        "u1",
        filename="note.txt",
        content_type="text/plain",
        data=b"hello diary",
    )
    assert meta["id"]
    assert store.get("u1", meta["id"])["text_preview"] == "hello diary"


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
