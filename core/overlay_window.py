"""Always-on-top corner HUD for the hub PC (tkinter).

Run:  python -m core.overlay_window --url http://127.0.0.1:8743
"""

from __future__ import annotations

import argparse
import json
import sys
import tkinter as tk
import urllib.request
from typing import Any


def _fetch(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "NexusOverlay/1.0"})
    with urllib.request.urlopen(req, timeout=4) as resp:
        return json.loads(resp.read().decode("utf-8"))


class OverlayApp:
    def __init__(self, stats_url: str, *, corner: str = "top-right") -> None:
        self.stats_url = stats_url
        self.root = tk.Tk()
        self.root.title("Nexus Overlay")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-alpha", 0.92)
        except tk.TclError:
            pass
        self.root.configure(bg="#0b1f1c")

        self.frame = tk.Frame(self.root, bg="#0b1f1c", padx=10, pady=8)
        self.frame.pack(fill="both", expand=True)

        self.title_lbl = tk.Label(
            self.frame,
            text="Nexus",
            fg="#3ecf8e",
            bg="#0b1f1c",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )
        self.title_lbl.pack(fill="x")

        self.body = tk.Label(
            self.frame,
            text="…",
            fg="#e8f5ef",
            bg="#0b1f1c",
            font=("Consolas", 9),
            justify="left",
            anchor="nw",
        )
        self.body.pack(fill="both", expand=True)

        self._drag_x = 0
        self._drag_y = 0
        for w in (self.root, self.frame, self.title_lbl, self.body):
            w.bind("<ButtonPress-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)
            w.bind("<Double-Button-1>", lambda _e: self.root.destroy())

        self.root.after(100, lambda: self._place(corner))
        self._tick()

    def _place(self, corner: str) -> None:
        self.root.update_idletasks()
        w, h = 220, 150
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        margin = 12
        if corner == "top-left":
            x, y = margin, margin
        elif corner == "bottom-right":
            x, y = sw - w - margin, sh - h - margin - 40
        elif corner == "bottom-left":
            x, y = margin, sh - h - margin - 40
        else:
            x, y = sw - w - margin, margin
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _start_drag(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _on_drag(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        self.root.geometry(f"+{event.x_root - self._drag_x}+{event.y_root - self._drag_y}")

    def _tick(self) -> None:
        try:
            data = _fetch(self.stats_url)
            cpu = data.get("cpu_percent", "?")
            mem = (data.get("memory") or {}).get("percent", "?")
            disk = (data.get("disk") or {}).get("percent", "?")
            games = data.get("games_running") or []
            top = data.get("top_processes") or []
            host = data.get("hostname") or "PC"
            lines = [
                f"{host}",
                f"CPU  {cpu}%",
                f"RAM  {mem}%",
                f"DISK {disk}%",
            ]
            if games:
                g0 = games[0]
                lines.append(f"GAME {g0.get('name')} {g0.get('cpu_percent')}%")
            elif top:
                t0 = top[0]
                lines.append(f"TOP  {t0.get('name')} {t0.get('cpu_percent')}%")
            lines.append("dbl-click: close")
            self.body.config(text="\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            self.body.config(text=f"offline\n{exc}")
        self.root.after(2000, self._tick)

    def run(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Nexus Node PC stats overlay")
    p.add_argument(
        "--url",
        default="http://127.0.0.1:8743",
        help="Hub base URL",
    )
    p.add_argument(
        "--corner",
        default="top-right",
        choices=["top-right", "top-left", "bottom-right", "bottom-left"],
    )
    args = p.parse_args(argv)
    base = args.url.rstrip("/")
    stats = f"{base}/api/overlay/stats"
    OverlayApp(stats, corner=args.corner).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
