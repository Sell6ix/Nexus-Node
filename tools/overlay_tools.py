"""Open the hub PC stats overlay (corner HUD)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.config import load_settings
from core.registry import tool


def _hub_base() -> str:
    host = os.getenv("NEXUS_PUBLIC_URL") or ""
    if host.strip():
        return host.rstrip("/")
    port = os.getenv("NEXUS_WEB_PORT", "8743")
    return f"http://127.0.0.1:{port}"


@tool
def open_stats_overlay(corner: str = "top-right", mode: str = "window") -> dict[str, Any]:
    """Open a small always-on-top PC stats HUD on this hub machine.

    Shows CPU, RAM, disk, and detected game processes. Useful while gaming.

    Args:
        corner: ``top-right`` (default), ``top-left``, ``bottom-right``, ``bottom-left``.
        mode: ``window`` = native always-on-top (tkinter); ``browser`` = /overlay page.

    Returns:
        Launch status and URLs.
    """
    try:
        base = _hub_base()
        overlay_url = f"{base}/overlay.html"
        corner_s = (corner or "top-right").strip().lower()
        if corner_s not in {"top-right", "top-left", "bottom-right", "bottom-left"}:
            corner_s = "top-right"
        mode_s = (mode or "window").strip().lower()

        if mode_s == "browser":
            # Prefer Chrome/Edge app window when available
            candidates: list[list[str]] = []
            if sys.platform.startswith("win"):
                candidates = [
                    ["cmd", "/c", "start", "", overlay_url],
                ]
            elif sys.platform == "darwin":
                candidates = [["open", overlay_url]]
            else:
                for bin_name in ("chromium", "google-chrome", "chromium-browser", "xdg-open"):
                    candidates.append([bin_name, overlay_url])
            last_err = ""
            for cmd in candidates:
                try:
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return {
                        "ok": True,
                        "mode": "browser",
                        "url": overlay_url,
                        "note": "Opened overlay page — pin/always-on-top in the window menu if you want.",
                    }
                except Exception as exc:  # noqa: BLE001
                    last_err = str(exc)
            return {"ok": False, "error": last_err or "Could not open browser", "url": overlay_url}

        # Native tkinter HUD (works without browser chrome)
        root = Path(__file__).resolve().parent.parent
        py = sys.executable
        cmd = [
            py,
            "-m",
            "core.overlay_window",
            "--url",
            base if "127.0.0.1" in base or "localhost" in base else f"http://127.0.0.1:{os.getenv('NEXUS_WEB_PORT', '8743')}",
            "--corner",
            corner_s,
        ]
        subprocess.Popen(
            cmd,
            cwd=str(root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {
            "ok": True,
            "mode": "window",
            "corner": corner_s,
            "stats_url": f"{cmd[cmd.index('--url') + 1]}/api/overlay/stats",
            "web_fallback": overlay_url,
            "note": "Always-on-top HUD launched. Drag to move; double-click to close.",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def get_overlay_stats() -> dict[str, Any]:
    """Return the same CPU/RAM/disk/game snapshot the corner overlay uses."""
    try:
        from core.overlay_stats import collect_overlay_stats

        return collect_overlay_stats()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
