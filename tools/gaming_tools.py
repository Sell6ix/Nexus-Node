"""Steam / gaming tools: launch, process checks, and steamcmd updates."""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Any

import psutil

from core.config import load_settings
from core.confirmation import require_confirmation
from core.registry import tool


def _steam_uri(app_id: str) -> str:
    return f"steam://rungameid/{app_id}"


@tool
def launch_steam_game(app_id: str) -> dict[str, Any]:
    """Launch a Steam game by its numeric App ID using the steam:// protocol.

    Args:
        app_id: Steam application ID (digits only), e.g. "570" for Dota 2.

    Returns:
        A dict describing whether the launch request was issued.
    """
    try:
        if not str(app_id).isdigit():
            return {"ok": False, "error": "app_id must be a numeric Steam App ID string."}

        uri = _steam_uri(str(app_id))
        if sys.platform.startswith("linux"):
            # xdg-open handles steam:// when Steam is installed.
            opener = "xdg-open"
            if shutil.which(opener) is None:
                return {
                    "ok": False,
                    "error": "xdg-open not found; cannot open steam:// URI on this system.",
                    "uri": uri,
                }
            result = subprocess.run(
                [opener, uri],
                capture_output=True,
                text=True,
                timeout=30.0,
                check=False,
            )
        elif sys.platform == "darwin":
            result = subprocess.run(
                ["open", uri],
                capture_output=True,
                text=True,
                timeout=30.0,
                check=False,
            )
        elif sys.platform.startswith("win"):
            # os.startfile equivalent via cmd start
            result = subprocess.run(
                ["cmd", "/c", "start", "", uri],
                capture_output=True,
                text=True,
                timeout=30.0,
                check=False,
            )
        else:
            return {"ok": False, "error": f"Unsupported platform: {sys.platform}", "uri": uri}

        if result.returncode != 0:
            return {
                "ok": False,
                "error": (result.stderr or result.stdout or "Failed to open steam:// URI").strip(),
                "uri": uri,
            }
        return {"ok": True, "app_id": str(app_id), "uri": uri, "note": "Launch request sent to Steam."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "app_id": app_id}


@tool
def check_game_running(process_name: str) -> dict[str, Any]:
    """Check whether a game (or any) process is currently running.

    Matches case-insensitively against process name and executable basename.

    Args:
        process_name: Process name to look for, e.g. "dota2" or "steam".

    Returns:
        A dict with running flag and matching process ids/names.
    """
    try:
        needle = process_name.lower().strip()
        if not needle:
            return {"ok": False, "error": "process_name must not be empty."}

        matches: list[dict[str, Any]] = []
        for proc in psutil.process_iter(["pid", "name", "exe", "status"]):
            try:
                info = proc.info
                name = (info.get("name") or "").lower()
                exe = (info.get("exe") or "").lower()
                if needle in name or needle in exe.split("/")[-1].split("\\")[-1]:
                    matches.append(
                        {
                            "pid": info.get("pid"),
                            "name": info.get("name"),
                            "exe": info.get("exe"),
                            "status": info.get("status"),
                        }
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        return {
            "ok": True,
            "process_name": process_name,
            "running": bool(matches),
            "match_count": len(matches),
            "matches": matches[:25],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "process_name": process_name}


@tool
@require_confirmation(
    "Run steamcmd to update/download a game? This may use significant bandwidth and disk."
)
def update_game_via_steamcmd(app_id: str) -> dict[str, Any]:
    """Update or install a Steam app using steamcmd (+app_update).

    Dangerous: downloads/updates game files via an external CLI. Requires confirmation.
    Anonymous login is used unless STEAM_USERNAME / STEAM_PASSWORD are set.

    Args:
        app_id: Steam application ID to update.

    Returns:
        A dict with steamcmd output summary or error details.
    """
    try:
        if not str(app_id).isdigit():
            return {"ok": False, "error": "app_id must be a numeric Steam App ID string."}

        settings = load_settings()
        steamcmd = settings.steamcmd_path
        if shutil.which(steamcmd) is None and steamcmd == "steamcmd":
            return {
                "ok": False,
                "error": (
                    "steamcmd not found on PATH. Install SteamCMD and/or set STEAMCMD_PATH."
                ),
                "app_id": str(app_id),
            }

        import os

        username = os.getenv("STEAM_USERNAME")
        password = os.getenv("STEAM_PASSWORD")
        if username and password:
            login = ["+login", username, password]
        else:
            login = ["+login", "anonymous"]

        cmd = [
            steamcmd,
            *login,
            "+app_update",
            str(app_id),
            "validate",
            "+quit",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600.0,
            check=False,
        )
        output = (result.stdout or result.stderr or "").strip()
        # Avoid echoing credentials if they somehow appear.
        if password:
            output = output.replace(password, "***")

        return {
            "ok": result.returncode == 0,
            "app_id": str(app_id),
            "returncode": result.returncode,
            "output_tail": "\n".join(output.splitlines()[-40:]),
            "error": None if result.returncode == 0 else "steamcmd exited non-zero",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "app_id": app_id}
