"""Everyday desktop tools: files, URLs, notifications, confirmed shell."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.confirmation import require_confirmation
from core.registry import tool


def _expand(path: str) -> Path:
    return Path(path).expanduser().resolve()


@tool
def list_directory(path: str = ".", max_entries: int = 100) -> dict[str, Any]:
    """List files and folders in a directory on the host PC (read-only).

    Args:
        path: Directory path (default: current working directory).
        max_entries: Maximum number of entries to return (default 100).

    Returns:
        A dict with entries (name, type, size_bytes) or an error.
    """
    try:
        root = _expand(path)
        if not root.exists():
            return {"ok": False, "error": f"Path does not exist: {root}"}
        if not root.is_dir():
            return {"ok": False, "error": f"Not a directory: {root}"}

        entries: list[dict[str, Any]] = []
        for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            try:
                item: dict[str, Any] = {
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "path": str(child),
                }
                if child.is_file():
                    item["size_bytes"] = child.stat().st_size
                entries.append(item)
            except OSError:
                continue
            if len(entries) >= max(1, min(max_entries, 500)):
                break

        return {
            "ok": True,
            "path": str(root),
            "count": len(entries),
            "entries": entries,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "path": path}


@tool
def read_text_file(path: str, max_bytes: int = 16000) -> dict[str, Any]:
    """Read a text file from the host PC (read-only, size-capped).

    Args:
        path: File path to read.
        max_bytes: Maximum bytes to return (default 16000).

    Returns:
        A dict with text content or an error.
    """
    try:
        target = _expand(path)
        if not target.is_file():
            return {"ok": False, "error": f"File not found: {target}"}

        size = target.stat().st_size
        limit = max(1, min(int(max_bytes), 200_000))
        data = target.read_bytes()[:limit]
        text = data.decode("utf-8", errors="replace")
        return {
            "ok": True,
            "path": str(target),
            "size_bytes": size,
            "truncated": size > limit,
            "text": text,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "path": path}


@tool
def open_url(url: str) -> dict[str, Any]:
    """Open a URL in the host PC's default browser.

    Args:
        url: Absolute http(s) URL to open.

    Returns:
        A dict describing whether the open request was issued.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return {"ok": False, "error": "Only http/https URLs are allowed.", "url": url}

        if sys.platform.startswith("linux"):
            opener = "xdg-open"
            if shutil.which(opener) is None:
                return {"ok": False, "error": "xdg-open not found.", "url": url}
            cmd = [opener, url]
        elif sys.platform == "darwin":
            cmd = ["open", url]
        elif sys.platform.startswith("win"):
            cmd = ["cmd", "/c", "start", "", url]
        else:
            return {"ok": False, "error": f"Unsupported platform: {sys.platform}", "url": url}

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30.0, check=False)
        if result.returncode != 0:
            return {
                "ok": False,
                "error": (result.stderr or result.stdout or "Failed to open URL").strip(),
                "url": url,
            }
        return {"ok": True, "url": url}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "url": url}


def _xml_escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _windows_toast_script(title: str, message: str) -> str:
    """WinRT toast (non-blocking). App id is PowerShell so no extra install."""
    t = _xml_escape(title)[:80]
    m = _xml_escape(message)[:240]
    xml = (
        "<toast><visual><binding template=\"ToastGeneric\">"
        f"<text>{t}</text><text>{m}</text>"
        "</binding></visual></toast>"
    )
    # Keep XML in single-quoted PowerShell string; we already escaped quotes.
    return (
        "$ErrorActionPreference = 'Stop'; "
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null; "
        f"$xml = New-Object Windows.Data.Xml.Dom.XmlDocument; $xml.LoadXml('{xml}'); "
        "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'; "
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($toast)"
    )


@tool
def notify_desktop(title: str, message: str) -> dict[str, Any]:
    """Show a desktop notification on the host PC.

    Windows uses a toast (not a blocking MessageBox). Linux: notify-send. macOS: osascript.

    Args:
        title: Notification title.
        message: Notification body text.

    Returns:
        A dict with ok status and the backend used.
    """
    try:
        if sys.platform.startswith("linux"):
            if shutil.which("notify-send") is None:
                return {
                    "ok": False,
                    "error": "notify-send not found (install libnotify).",
                    "title": title,
                }
            cmd = ["notify-send", title, message]
            backend = "notify-send"
        elif sys.platform == "darwin":
            script = f"display notification {message!r} with title {title!r}"
            cmd = ["osascript", "-e", script]
            backend = "osascript"
        elif sys.platform.startswith("win"):
            cmd = [
                "powershell",
                "-NoProfile",
                "-WindowStyle",
                "Hidden",
                "-Command",
                _windows_toast_script(title, message),
            ]
            backend = "windows-toast"
        else:
            return {"ok": False, "error": f"Unsupported platform: {sys.platform}"}

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15.0, check=False)
        if result.returncode != 0:
            if sys.platform.startswith("win"):
                # Auto-closing popup fallback (still not a modal app freeze).
                fallback = (
                    "powershell",
                    "-NoProfile",
                    "-WindowStyle",
                    "Hidden",
                    "-Command",
                    (
                        f"(New-Object -ComObject Wscript.Shell).Popup("
                        f"{message!r}, 6, {title!r}, 64) | Out-Null"
                    ),
                )
                fb = subprocess.run(fallback, capture_output=True, text=True, timeout=12.0, check=False)
                if fb.returncode == 0:
                    return {
                        "ok": True,
                        "backend": "wscript-popup",
                        "title": title,
                        "message": message,
                    }
            return {
                "ok": False,
                "error": (result.stderr or result.stdout or "notify failed").strip(),
                "backend": backend,
            }
        return {"ok": True, "backend": backend, "title": title, "message": message}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "title": title}


@tool
@require_confirmation(
    "Run a shell command on the host PC? This can change the system."
)
def run_shell(command: str, timeout_seconds: float = 60.0) -> dict[str, Any]:
    """Run a shell command on the host PC. Requires confirmation.

    Dangerous: executes arbitrary commands. Prefer safer specialized tools when possible.
    Blocked by safety policy (deny patterns / optional allowlist) even if confirmed.

    Args:
        command: Shell command string to execute.
        timeout_seconds: Kill the process after this many seconds (default 60).

    Returns:
        A dict with returncode, stdout/stderr tails, or an error.
    """
    try:
        from core.config import load_settings
        from core.safety import check_shell_command

        gate = check_shell_command(command, load_settings().safety)
        if not gate.get("ok"):
            return {"ok": False, "error": gate.get("error"), "command": command}

        timeout = max(1.0, min(float(timeout_seconds), 600.0))
        if sys.platform.startswith("win"):
            completed = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        else:
            completed = subprocess.run(
                command,
                shell=True,
                executable=os.environ.get("SHELL", "/bin/bash"),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )

        stdout = (completed.stdout or "")[-4000:]
        stderr = (completed.stderr or "")[-4000:]
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "command": command,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Command timed out.", "command": command}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "command": command}
