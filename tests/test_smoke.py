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


def test_upload_request_body_limit() -> None:
    import asyncio

    from core.web_hub import UploadRequestLimitMiddleware

    received = bytearray()
    messages = [
        {"type": "http.request", "body": b"123", "more_body": True},
        {"type": "http.request", "body": b"456", "more_body": False},
    ]
    sent = []

    async def downstream(scope, limited_receive, send) -> None:
        while True:
            message = await limited_receive()
            received.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break

    async def receive():
        return messages.pop(0)

    async def send(message) -> None:
        sent.append(message)

    middleware = UploadRequestLimitMiddleware(downstream)
    middleware.max_body_by_path["/api/uploads"] = 5
    asyncio.run(
        middleware(
            {"type": "http", "method": "POST", "path": "/api/uploads", "headers": []},
            receive,
            send,
        )
    )
    assert received == b"123"
    assert sent[0]["status"] == 413


def test_upload_request_concurrency_is_bounded() -> None:
    import asyncio
    import threading

    from core.web_hub import UploadRequestLimitMiddleware

    middleware = UploadRequestLimitMiddleware(None)
    middleware._active_requests = threading.BoundedSemaphore(1)
    assert middleware._active_requests.acquire(blocking=False)
    sent = []

    async def unused_receive():
        raise AssertionError("rejected request must not read the body")

    async def send(message) -> None:
        sent.append(message)

    async def unused_app(scope, receive, send) -> None:
        raise AssertionError("request must be rejected before app dispatch")

    middleware.app = unused_app
    asyncio.run(
        middleware(
            {"type": "http", "method": "POST", "path": "/api/uploads", "headers": []},
            unused_receive,
            send,
        )
    )
    middleware._active_requests.release()
    assert sent[0]["status"] == 503


def test_login_failure_throttle_bounds_state_and_resets(monkeypatch) -> None:
    import core.web_hub as web_hub

    limiter = web_hub.LoginFailureThrottle(
        window_seconds=900, max_entries=2, base_delay=0.25, max_delay=1
    )
    now = 100.0
    monkeypatch.setattr(web_hub.time, "monotonic", lambda: now)
    alice = web_hub._login_limiter_key("192.0.2.1", "Alice")
    bob = web_hub._login_limiter_key("192.0.2.1", "Bob")
    charlie = web_hub._login_limiter_key("192.0.2.1", "Charlie")
    assert limiter.record_failure(alice) == 0.25
    assert limiter.record_failure(alice) == 0.5
    assert limiter.record_failure(alice) == 1
    assert limiter.record_failure(alice) == 1
    limiter.clear(alice)
    assert limiter.record_failure(alice) == 0.25
    now += 901
    assert limiter.record_failure(alice) == 0.25
    limiter.record_failure(bob)
    limiter.record_failure(charlie)
    assert len(limiter._attempts) == 2


def test_login_failure_throttle_normalizes_usernames() -> None:
    from core.web_hub import LoginFailureThrottle, _login_limiter_key

    limiter = LoginFailureThrottle()
    alice = _login_limiter_key("192.0.2.1", "Alice")
    alice_normalized = _login_limiter_key("192.0.2.1", " alice ")
    assert alice == alice_normalized
    assert limiter.record_failure(alice) == 0.25
    assert limiter.record_failure(alice_normalized) == 0.5


def test_login_failures_do_not_lock_out_valid_credentials(monkeypatch, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from core.config import load_settings
    from core.registry import ToolRegistry
    from core.web_hub import LoginFailureThrottle, create_app

    monkeypatch.setenv("NEXUS_DATA_DIR", str(tmp_path / "data"))
    original_record_failure = LoginFailureThrottle.record_failure

    def record_failure_without_wait(self, key: str) -> float:
        original_record_failure(self, key)
        return 0

    monkeypatch.setattr(LoginFailureThrottle, "record_failure", record_failure_without_wait)
    app = create_app(load_settings(), ToolRegistry(), open_signup=True)
    with TestClient(app) as client:
        client.post(
            "/api/auth/register",
            json={"username": "login-owner", "password": "password1"},
        )
        client.post(
            "/api/auth/register",
            json={"username": "login-member", "password": "password2"},
        )
        for _ in range(5):
            response = client.post(
                "/api/auth/login",
                json={"username": "login-owner", "password": "wrong-pass"},
            )
            assert response.status_code == 401
        valid = client.post(
            "/api/auth/login",
            json={"username": "login-owner", "password": "password1"},
        )
        assert valid.status_code == 200
        invalid_after_success = client.post(
            "/api/auth/login",
            json={"username": "login-owner", "password": "wrong-pass"},
        )
        assert invalid_after_success.status_code == 401
        other_user = client.post(
            "/api/auth/login",
            json={"username": "login-member", "password": "password2"},
        )
        assert other_user.status_code == 200


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


def test_fetch_url_rejects_non_public_destinations(monkeypatch) -> None:
    import socket
    import pytest

    from tools.web_tools import _resolve_public_url

    def resolve(host, port, *, type):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with pytest.raises(ValueError, match="non-public"):
        _resolve_public_url("http://public.example/path")

    for url in (
        "file:///etc/passwd",
        "http://user:pass@example.com/",
        "http://example.com\\@127.0.0.1/",
        "http://[fe80::1%25eth0]/",
    ):
        with pytest.raises(ValueError):
            _resolve_public_url(url)


def test_fetch_url_revalidates_redirect_destinations(monkeypatch) -> None:
    import email.message
    import urllib.error
    import urllib.parse
    import pytest

    from tools import web_tools

    calls = []
    resolve_public_url = web_tools._resolve_public_url

    def resolve_url(url):
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname == "example.com":
            return parsed, "8.8.8.8"
        return resolve_public_url(url)

    def open_public(parsed, pinned_ip, timeout):
        calls.append(parsed.geturl())
        headers = email.message.Message()
        headers["Location"] = "http://127.0.0.1/internal"
        raise urllib.error.HTTPError(
            parsed.geturl(), 302, "Found", headers, None
        )

    monkeypatch.setattr(web_tools, "_open_pinned", open_public)
    monkeypatch.setattr(web_tools, "_resolve_public_url", resolve_url)
    with pytest.raises(ValueError, match="non-public"):
        web_tools._open_public_url("http://example.com/public", timeout=1)
    assert calls == ["http://example.com/public"]


def test_fetch_url_returns_final_redirect_url(monkeypatch) -> None:
    from tools import web_tools

    monkeypatch.setattr(
        web_tools,
        "_http_text",
        lambda url: ("<title>Final page</title><p>Content</p>", "https://example.com/final"),
    )
    result = web_tools.fetch_url("https://example.com/start")
    assert result["ok"]
    assert result["url"] == "https://example.com/final"
    assert result["title"] == "Final page"


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
