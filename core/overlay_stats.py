"""Lightweight hub PC metrics for the corner overlay HUD."""

from __future__ import annotations

import os
import time
from typing import Any

import psutil

# Common game / launcher process substrings (case-insensitive).
_GAME_HINTS = (
    "steam",
    "steamwebhelper",
    "dota2",
    "cs2",
    "csgo",
    "valorant",
    "league",
    "riotclient",
    "epicgames",
    "fortnite",
    "gta5",
    "gtav",
    "rdr2",
    "cyberpunk",
    "eldenring",
    "minecraft",
    "javaw",  # often Minecraft
    "wow",
    "overwatch",
    "apex",
    "r5apex",
    "hl2",
    "unity",
    "unreal",
    "wine",
    "proton",
)


def _disk_path() -> str:
    if os.name == "nt":
        return os.environ.get("SystemDrive", "C:") + "\\"
    return "/"


def collect_overlay_stats() -> dict[str, Any]:
    """Fast snapshot for HUD polling (no long CPU interval)."""
    # Non-blocking CPU: first call after import may be 0; callers poll often.
    cpu = psutil.cpu_percent(interval=None)
    if cpu == 0.0:
        cpu = psutil.cpu_percent(interval=0.15)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(_disk_path())

    procs: list[dict[str, Any]] = []
    games: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            info = proc.info
            name = str(info.get("name") or "")
            low = name.lower()
            row = {
                "pid": info.get("pid"),
                "name": name,
                "cpu_percent": float(info.get("cpu_percent") or 0.0),
                "memory_percent": round(float(info.get("memory_percent") or 0.0), 1),
            }
            procs.append(row)
            if any(h in low for h in _GAME_HINTS):
                games.append(row)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    procs.sort(key=lambda p: p["cpu_percent"], reverse=True)
    games.sort(key=lambda p: p["cpu_percent"], reverse=True)

    # Deduplicate game names
    seen: set[str] = set()
    games_unique: list[dict[str, Any]] = []
    for g in games:
        key = str(g["name"]).lower()
        if key in seen:
            continue
        seen.add(key)
        games_unique.append(g)

    return {
        "ok": True,
        "ts": time.time(),
        "hostname": (
            os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME")
        ),
        "cpu_percent": round(float(cpu), 1),
        "cpu_count": psutil.cpu_count(logical=True),
        "memory": {
            "percent": round(float(mem.percent), 1),
            "used_gb": round((mem.total - mem.available) / (1024**3), 1),
            "total_gb": round(mem.total / (1024**3), 1),
        },
        "disk": {
            "percent": round(float(disk.percent), 1),
            "path": _disk_path(),
        },
        "games_running": games_unique[:6],
        "top_processes": procs[:5],
    }
