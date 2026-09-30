"""Per-user prefs (home address for routes, etc.)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class PrefsStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, user_id: str) -> Path:
        safe = re.sub(r"[^\w\-]", "_", str(user_id)) or "user"
        return self.root / f"{safe}.json"

    def get(self, user_id: str) -> dict[str, Any]:
        path = self._path(user_id)
        if not path.is_file():
            return {
                "home_address": "Tel Aviv",
                "home_lat": None,
                "home_lon": None,
                "timezone": "Asia/Jerusalem",
            }
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        return {
            "home_address": str(raw.get("home_address") or "Tel Aviv"),
            "home_lat": raw.get("home_lat"),
            "home_lon": raw.get("home_lon"),
            "timezone": str(raw.get("timezone") or "Asia/Jerusalem"),
        }

    def set(self, user_id: str, **fields: Any) -> dict[str, Any]:
        cur = self.get(user_id)
        for key in ("home_address", "home_lat", "home_lon", "timezone"):
            if key in fields and fields[key] is not None:
                cur[key] = fields[key]
        if "home_address" in fields and fields["home_address"] is not None:
            cur["home_address"] = str(fields["home_address"]).strip() or "Tel Aviv"
        self._path(user_id).write_text(
            json.dumps(cur, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return cur
