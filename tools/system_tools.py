"""System monitoring, process control, and Telegram file backup tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import psutil

from core.config import load_settings
from core.confirmation import ConfirmationDenied, confirm_action
from core.registry import tool

ProcessAction = Literal["check", "kill"]


def _primary_disk_path() -> str:
    """Pick a sensible disk root for the current OS."""
    if os.name == "nt":
        return os.environ.get("SystemDrive", "C:") + "\\"
    return "/"


@tool
def get_system_stats() -> dict[str, Any]:
    """Collect CPU, memory, disk, and top-process stats for this machine.

    Uses psutil. Safe read-only inspection — no confirmation required.
    Useful from phone (Telegram) to check PC health remotely.

    Returns:
        A dict with cpu_percent, memory, disk, boot_time, and top processes.
    """
    try:
        memory = psutil.virtual_memory()
        disk_path = _primary_disk_path()
        disk = psutil.disk_usage(disk_path)
        cpu = psutil.cpu_percent(interval=0.2)

        top: list[dict[str, Any]] = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            try:
                info = proc.info
                top.append(
                    {
                        "pid": info.get("pid"),
                        "name": info.get("name"),
                        "cpu_percent": info.get("cpu_percent") or 0.0,
                        "memory_percent": round(float(info.get("memory_percent") or 0.0), 2),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        top.sort(key=lambda p: p["cpu_percent"], reverse=True)
        return {
            "ok": True,
            "hostname": os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME"),
            "cpu_percent": cpu,
            "cpu_count": psutil.cpu_count(logical=True),
            "memory": {
                "total_gb": round(memory.total / (1024**3), 2),
                "available_gb": round(memory.available / (1024**3), 2),
                "percent": memory.percent,
            },
            "disk": {
                "path": disk_path,
                "total_gb": round(disk.total / (1024**3), 2),
                "used_gb": round(disk.used / (1024**3), 2),
                "percent": disk.percent,
            },
            "boot_time": psutil.boot_time(),
            "top_processes": top[:10],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def manage_process(
    process_name: str,
    action: ProcessAction = "check",
) -> dict[str, Any]:
    """Check or kill processes by name on the host PC.

    Use action="check" (default) to list matching processes.
    Use action="kill" to terminate matches — requires confirmation
    (console on PC, Telegram Yes/No buttons from phone).

    Args:
        process_name: Process name substring to match (case-insensitive).
        action: "check" to inspect, or "kill" to terminate matching processes.

    Returns:
        A dict with matches and, for kill, termination results.
    """
    try:
        if action not in {"check", "kill"}:
            return {"ok": False, "error": f"Unsupported action: {action}"}

        needle = process_name.lower().strip()
        if not needle:
            return {"ok": False, "error": "process_name must not be empty."}

        matches: list[psutil.Process] = []
        summaries: list[dict[str, Any]] = []
        for proc in psutil.process_iter(["pid", "name", "exe", "status"]):
            try:
                info = proc.info
                name = (info.get("name") or "").lower()
                exe = (info.get("exe") or "").lower()
                if needle in name or needle in exe.split("/")[-1].split("\\")[-1]:
                    matches.append(proc)
                    summaries.append(
                        {
                            "pid": info.get("pid"),
                            "name": info.get("name"),
                            "exe": info.get("exe"),
                            "status": info.get("status"),
                        }
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        if action == "check":
            return {
                "ok": True,
                "action": "check",
                "process_name": process_name,
                "match_count": len(summaries),
                "matches": summaries[:50],
            }

        prompt = (
            f"Kill {len(summaries)} process(es) matching {process_name!r}? "
            f"pids={[s['pid'] for s in summaries[:20]]}"
        )
        if not confirm_action(prompt, use_telegram_stub=True):
            raise ConfirmationDenied(
                f"User denied confirmation to kill processes matching {process_name!r}."
            )

        results: list[dict[str, Any]] = []
        for proc, summary in zip(matches, summaries, strict=False):
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                    results.append({**summary, "killed": True, "method": "terminate"})
                except psutil.TimeoutExpired:
                    proc.kill()
                    results.append({**summary, "killed": True, "method": "kill"})
            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                results.append({**summary, "killed": False, "error": str(exc)})

        return {
            "ok": True,
            "action": "kill",
            "process_name": process_name,
            "results": results,
        }
    except ConfirmationDenied as exc:
        return {"ok": False, "error": str(exc), "process_name": process_name, "action": action}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "process_name": process_name, "action": action}


@tool
def send_telegram_backup(file_path: str) -> dict[str, Any]:
    """Send a local PC file to Telegram as a document via the Bot API.

    Uses TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from the environment.
    If secrets are missing, returns a clear mock/local fallback result instead
    of failing hard — useful for offline demos.
    From phone, this is a good way to pull a file off the PC.

    Args:
        file_path: Path to the file on the host PC to upload as a Telegram document.

    Returns:
        A dict with ok status, telegram message metadata, or mock fallback info.
    """
    try:
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            return {"ok": False, "error": f"File not found: {path}"}

        settings = load_settings()
        if not settings.has_telegram:
            return {
                "ok": True,
                "mock": True,
                "file_path": str(path),
                "size_bytes": path.stat().st_size,
                "note": (
                    "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set. "
                    "Skipped real upload; local mock backup acknowledged."
                ),
            }

        from core.telegram_api import TelegramAPIError, TelegramClient

        client = TelegramClient(settings.telegram_bot_token)  # type: ignore[arg-type]
        try:
            result = client.send_document(settings.telegram_chat_id, path)  # type: ignore[arg-type]
        except TelegramAPIError as exc:
            return {"ok": False, "error": str(exc), "file_path": str(path)}

        return {
            "ok": True,
            "mock": False,
            "file_path": str(path),
            "telegram_message_id": (result or {}).get("message_id"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "file_path": file_path}
