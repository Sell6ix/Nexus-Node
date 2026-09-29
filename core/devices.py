"""Multi-device registry and remote tool execution client."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Device:
    id: str
    name: str
    base_url: str | None = None  # None / empty = local process
    token: str | None = None
    owner_chat_id: str | None = None
    enabled: bool = True

    @property
    def is_local(self) -> bool:
        return not (self.base_url or "").strip()


@dataclass
class DeviceRegistry:
    path: Path
    local_id: str
    local_name: str
    local_token: str | None = None
    devices: dict[str, Device] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()
        # Ensure local device always present.
        if self.local_id not in self.devices:
            self.devices[self.local_id] = Device(
                id=self.local_id,
                name=self.local_name,
                base_url=None,
                token=self.local_token,
            )
            self._save()
        else:
            local = self.devices[self.local_id]
            local.name = self.local_name or local.name
            if self.local_token:
                local.token = self.local_token
            self._save()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        items = raw.get("devices", []) if isinstance(raw, dict) else raw
        for item in items or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            dev = Device(
                id=str(item["id"]),
                name=str(item.get("name") or item["id"]),
                base_url=item.get("base_url") or None,
                token=item.get("token") or None,
                owner_chat_id=item.get("owner_chat_id") or None,
                enabled=bool(item.get("enabled", True)),
            )
            self.devices[dev.id] = dev

    def _save(self) -> None:
        payload = {"devices": [asdict(d) for d in self.devices.values()]}
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def list_devices(self) -> list[Device]:
        return sorted(self.devices.values(), key=lambda d: d.id)

    def get(self, device_id: str | None) -> Device | None:
        if not device_id:
            return self.devices.get(self.local_id)
        return self.devices.get(device_id)

    def upsert(
        self,
        device_id: str,
        *,
        name: str | None = None,
        base_url: str | None = None,
        token: str | None = None,
        owner_chat_id: str | None = None,
        enabled: bool = True,
    ) -> Device:
        existing = self.devices.get(device_id)
        dev = Device(
            id=device_id,
            name=name or (existing.name if existing else device_id),
            base_url=base_url if base_url is not None else (existing.base_url if existing else None),
            token=token if token is not None else (existing.token if existing else None),
            owner_chat_id=owner_chat_id
            if owner_chat_id is not None
            else (existing.owner_chat_id if existing else None),
            enabled=enabled,
        )
        self.devices[device_id] = dev
        self._save()
        return dev

    def remove(self, device_id: str) -> bool:
        if device_id == self.local_id:
            return False
        if device_id not in self.devices:
            return False
        del self.devices[device_id]
        self._save()
        return True

    def visible_to(self, chat_id: int | str, *, is_owner: bool) -> list[Device]:
        """Owner sees all; others only unowned or devices they own."""
        key = str(chat_id)
        out: list[Device] = []
        for dev in self.list_devices():
            if not dev.enabled:
                continue
            if is_owner:
                out.append(dev)
            elif not dev.owner_chat_id or str(dev.owner_chat_id) == key:
                out.append(dev)
        return out


def execute_remote_tool(
    device: Device,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    confirmed: bool = False,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Call a worker device's ``/v1/tools/execute`` endpoint.

    Pass ``confirmed=True`` only after the hub owner approved a dangerous tool.
    """
    if device.is_local:
        return {"ok": False, "error": "Device is local; use in-process execution."}
    if not device.token:
        return {"ok": False, "error": f"Device {device.id} has no auth token configured."}

    url = device.base_url.rstrip("/") + "/v1/tools/execute"  # type: ignore[union-attr]
    body = json.dumps(
        {
            "tool": tool_name,
            "arguments": arguments or {},
            "confirmed": bool(confirmed),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {device.token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {"ok": False, "error": "Invalid worker response", "raw": raw[:500]}
        return parsed
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"Worker HTTP {exc.code}: {detail}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": f"Worker network error: {exc}"}
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"Worker JSON error: {exc}"}


def ping_device(device: Device, *, timeout: float = 5.0) -> dict[str, Any]:
    if device.is_local:
        return {"ok": True, "device": device.id, "mode": "local"}
    url = device.base_url.rstrip("/") + "/v1/health"  # type: ignore[union-attr]
    headers = {}
    if device.token:
        headers["Authorization"] = f"Bearer {device.token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        return {"ok": True, "device": device.id, "mode": "remote", "body": json.loads(raw)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "device": device.id, "error": str(exc)}
