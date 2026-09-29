"""HTTP worker — expose local tools so a hub device can call this PC."""

from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from core.confirmation import needs_confirmation, tool_requires_confirmation
from core.registry import ToolRegistry


class WorkerServer:
    """Authenticated tool-execution worker for multi-device setups."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        token: str,
        host: str = "127.0.0.1",
        port: int = 8765,
        device_id: str = "local",
        device_name: str = "Local PC",
    ) -> None:
        if not token:
            raise ValueError("Worker token is required")
        self.registry = registry
        self.token = token
        self.host = host
        self.port = port
        self.device_id = device_id
        self.device_name = device_name
        self._httpd: ThreadingHTTPServer | None = None

    def _authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        header = handler.headers.get("Authorization") or ""
        expected = f"Bearer {self.token}"
        return hmac.compare_digest(header, expected)

    def _execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        registered = self.registry.get(tool_name)
        if registered is None:
            return {"ok": False, "error": f"Unknown tool: {tool_name}"}
        try:
            validated = registered.parse_arguments(arguments or {})
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Invalid arguments: {exc}", "tool": tool_name}

        # Dangerous tools: hub confirms first, then RPC with confirmed=true.
        # Never prompt on the worker console (would hang headless workers).
        if tool_requires_confirmation(tool_name, registered.func, validated):
            if not confirmed:
                return {
                    "ok": False,
                    "error": (
                        f"Tool '{tool_name}' requires owner confirmation on the hub. "
                        "Re-run after approval with confirmed=true."
                    ),
                    "requires_confirmation": True,
                }
            target = (
                getattr(registered.func, "__wrapped__", registered.func)
                if needs_confirmation(registered.func)
                else registered.func
            )
            try:
                result = target(**validated)
                if isinstance(result, dict):
                    return result
                return {"ok": True, "result": result}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc), "tool": tool_name}

        try:
            result = registered.func(**validated)
            if isinstance(result, dict):
                return result
            return {"ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "tool": tool_name}

    def serve_forever(self) -> None:
        worker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                print(f"[worker] {self.address_string()} {fmt % args}")

            def _send(self, code: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                path = urlparse(self.path).path
                if path != "/v1/health":
                    self._send(404, {"ok": False, "error": "not found"})
                    return
                if not worker._authorized(self):
                    self._send(401, {"ok": False, "error": "unauthorized"})
                    return
                self._send(
                    200,
                    {
                        "ok": True,
                        "device_id": worker.device_id,
                        "device_name": worker.device_name,
                        "tools": len(worker.registry),
                    },
                )

            def do_POST(self) -> None:  # noqa: N802
                path = urlparse(self.path).path
                if path != "/v1/tools/execute":
                    self._send(404, {"ok": False, "error": "not found"})
                    return
                if not worker._authorized(self):
                    self._send(401, {"ok": False, "error": "unauthorized"})
                    return
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    self._send(400, {"ok": False, "error": "invalid json"})
                    return
                tool = str(body.get("tool") or "")
                args = body.get("arguments") or {}
                confirmed = bool(body.get("confirmed"))
                if not tool:
                    self._send(400, {"ok": False, "error": "tool required"})
                    return
                if not isinstance(args, dict):
                    self._send(400, {"ok": False, "error": "arguments must be object"})
                    return
                result = worker._execute(tool, args, confirmed=confirmed)
                self._send(200 if result.get("ok", True) else 200, result)

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        print(
            f"Worker listening on http://{self.host}:{self.port} "
            f"(device={self.device_id!r}). Token auth required."
        )
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            print("WARNING: worker is bound beyond localhost — protect your LAN/token.")
        self._httpd.serve_forever()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
