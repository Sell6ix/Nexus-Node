"""Android Debug Bridge (ADB) tools for device file transfer and status."""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

from core.config import load_settings
from core.confirmation import require_confirmation
from core.registry import tool


def _adb_bin() -> str:
    settings = load_settings()
    return settings.adb_path


def _run_adb(args: list[str], *, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    binary = _adb_bin()
    if shutil.which(binary) is None and binary == "adb":
        raise FileNotFoundError(
            "adb was not found on PATH. Install Android platform-tools "
            "or set ADB_PATH to the adb executable."
        )
    return subprocess.run(
        [binary, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@tool
def adb_get_device_status() -> dict[str, Any]:
    """Get connected Android device status via ADB.

    Returns device serials, connection state (device/offline/unauthorized),
    and a short summary suitable for telling the user whether ADB is ready.

    Returns:
        A dict with ok, devices (list), and summary fields.
    """
    try:
        result = _run_adb(["devices", "-l"], timeout=15.0)
        if result.returncode != 0:
            return {
                "ok": False,
                "error": (result.stderr or result.stdout or "adb devices failed").strip(),
            }

        devices: list[dict[str, str]] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.lower().startswith("list of devices"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, state = parts[0], parts[1]
            extra = " ".join(parts[2:]) if len(parts) > 2 else ""
            devices.append({"serial": serial, "state": state, "details": extra})

        ready = [d for d in devices if d["state"] == "device"]
        summary = (
            f"{len(ready)} device(s) ready"
            if ready
            else ("No ready devices" if devices else "No devices listed")
        )
        return {"ok": True, "devices": devices, "ready_count": len(ready), "summary": summary}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
@require_confirmation(
    "Push a local file to the Android device via ADB? This writes to device storage."
)
def adb_push_file(
    local_path: str,
    remote_path: str = "/sdcard/Download/",
) -> dict[str, Any]:
    """Push a local file to a connected Android device over ADB.

    Dangerous: writes files onto the device filesystem. Requires confirmation.

    Args:
        local_path: Absolute or relative path to the file on this PC.
        remote_path: Destination path or directory on the device
            (default: /sdcard/Download/).

    Returns:
        A dict with ok, remote_path, and adb output or error.
    """
    try:
        result = _run_adb(["push", local_path, remote_path], timeout=120.0)
        output = (result.stdout or result.stderr or "").strip()
        if result.returncode != 0:
            return {"ok": False, "error": output or "adb push failed", "local_path": local_path}
        return {
            "ok": True,
            "local_path": local_path,
            "remote_path": remote_path,
            "output": output,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "local_path": local_path}


@tool
def adb_send_notification_or_toast(message: str) -> dict[str, Any]:
    """Show a short toast/notification-like message on a connected Android device.

    Uses ``adb shell am broadcast`` / service call patterns that work on many
    devices without installing an app. Falls back to ``adb shell echo`` status
    if the toast broadcast is unsupported.

    Args:
        message: Short text to display on the device (keep under ~80 chars).

    Returns:
        A dict describing whether the toast/broadcast was sent.
    """
    try:
        # Prefer a generic toast via app_process / service; many images support
        # `cmd statusbar` or broadcast. We try toast via `am` service call first.
        safe = message.replace("'", "\\'")
        # Attempt 1: activity manager toast via service (works on some builds).
        toast_cmd = (
            "service call statusbar 1 s16 "
            f"'{safe}'"
        )
        result = _run_adb(["shell", toast_cmd], timeout=20.0)

        if result.returncode == 0:
            return {
                "ok": True,
                "method": "statusbar_service",
                "message": message,
                "output": (result.stdout or "").strip(),
            }

        # Attempt 2: broadcast a notification intent (often no-op without receiver,
        # but useful when a companion app listens).
        broadcast = _run_adb(
            [
                "shell",
                "am",
                "broadcast",
                "-a",
                "android.intent.action.SHOW_TOAST",
                "--es",
                "message",
                message,
            ],
            timeout=20.0,
        )
        if broadcast.returncode == 0:
            return {
                "ok": True,
                "method": "broadcast_SHOW_TOAST",
                "message": message,
                "output": (broadcast.stdout or broadcast.stderr or "").strip(),
                "note": "Broadcast sent; a companion receiver may be required to display UI.",
            }

        return {
            "ok": False,
            "error": (
                (result.stderr or result.stdout or "").strip()
                or (broadcast.stderr or broadcast.stdout or "").strip()
                or "Unable to send toast/notification via ADB"
            ),
            "message": message,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "message": message}
