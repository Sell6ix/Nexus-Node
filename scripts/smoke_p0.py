#!/usr/bin/env python3
"""Smoke checks for P0 fixes: daily isolation, device_add parse, confirm/remote."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.confirmation import needs_confirmation, tool_requires_confirmation
from core.context import request_context
from core.devices import Device, execute_remote_tool
from core.registry import ToolRegistry
from core.telegram_bot import _parse_device_add
from core.worker import WorkerServer
from tools import daily_tools, system_tools


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def test_device_add_parse() -> None:
    assert _parse_device_add(
        '/device_add laptop "Home Laptop" http://192.168.1.20:8765 tok'
    ) == ("laptop", "Home Laptop", "http://192.168.1.20:8765", "tok")
    assert _parse_device_add(
        "/device_add laptop|Home Laptop|http://192.168.1.20:8765|tok"
    ) == ("laptop", "Home Laptop", "http://192.168.1.20:8765", "tok")
    assert _parse_device_add(
        "/device_add laptop Home Laptop http://x:1 tok"
    ) == ("laptop", "Home Laptop", "http://x:1", "tok")
    assert _parse_device_add("/device_add only-two args") is None
    print("OK device_add parse")


def test_daily_isolation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        daily_tools._ROOT = Path(tmp)  # type: ignore[attr-defined]
        with request_context(user_id="alice"):
            r1 = daily_tools.add_todo("milk")
            assert r1["ok"], r1
        with request_context(user_id="bob"):
            r2 = daily_tools.add_todo("eggs")
            assert r2["ok"], r2
            listed = daily_tools.list_todos()
            assert listed["count"] == 1
            assert listed["todos"][0]["title"] == "eggs"
            assert listed["user_id"] == "bob"
        with request_context(user_id="alice"):
            listed = daily_tools.list_todos()
            assert listed["count"] == 1
            assert listed["todos"][0]["title"] == "milk"
    print("OK daily isolation")


def test_manage_process_confirm_flag() -> None:
    reg = ToolRegistry()
    reg.discover("tools")
    mp = reg.get("manage_process")
    kp = reg.get("kill_process")
    assert mp and kp
    assert not needs_confirmation(mp.func)
    assert needs_confirmation(kp.func)
    assert tool_requires_confirmation("manage_process", mp.func, {"action": "kill", "process_name": "x"})
    assert not tool_requires_confirmation(
        "manage_process", mp.func, {"action": "check", "process_name": "x"}
    )
    assert tool_requires_confirmation("kill_process", kp.func, {"process_name": "x"})
    print("OK confirmation flags")


def test_worker_confirmed_exec() -> None:
    reg = ToolRegistry()
    reg.discover("tools")
    token = "smoke-token-please-ignore"
    worker = WorkerServer(
        reg,
        token=token,
        host="127.0.0.1",
        port=18765,
        device_id="smoke-pc",
        device_name="Smoke",
    )

    # Patch serve to start server without blocking forever — use handler class via serve thread.
    class _H:
        pass

    # Reuse Worker's Handler by starting serve_forever in a thread briefly.
    t = threading.Thread(target=worker.serve_forever, daemon=True)
    t.start()
    import time

    time.sleep(0.3)
    device = Device(
        id="smoke-pc",
        name="Smoke",
        base_url="http://127.0.0.1:18765",
        token=token,
    )
    denied = execute_remote_tool(
        device,
        "manage_process",
        {"process_name": "nonexistent-nexus-smoke-xyz", "action": "kill"},
        confirmed=False,
    )
    if denied.get("ok") or not denied.get("requires_confirmation"):
        worker.stop()
        _fail(f"expected requires_confirmation, got {denied}")

    allowed = execute_remote_tool(
        device,
        "manage_process",
        {"process_name": "nonexistent-nexus-smoke-xyz", "action": "kill"},
        confirmed=True,
    )
    worker.stop()
    if not allowed.get("ok"):
        _fail(f"confirmed kill failed: {allowed}")
    if allowed.get("action") != "kill":
        _fail(f"unexpected action payload: {allowed}")
    print("OK worker confirmed exec")
    print(json.dumps({"denied_error": denied.get("error"), "allowed_ok": True}))


def main() -> int:
    test_device_add_parse()
    test_daily_isolation()
    test_manage_process_confirm_flag()
    test_worker_confirmed_exec()
    print("All P0 smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
