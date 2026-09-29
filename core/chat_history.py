"""Per-user chat transcript persistence for the web UI."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Literal

Role = Literal["user", "assistant", "system"]


class ChatHistory:
    """JSON transcripts under ``data/chats/<user_id>.json``."""

    def __init__(self, root: Path, *, max_messages: int = 200) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_messages = max(20, int(max_messages))

    def _path(self, user_id: str) -> Path:
        safe = re.sub(r"[^\w\-]", "_", str(user_id)) or "user"
        return self.root / f"{safe}.json"

    def load(self, user_id: str) -> list[dict[str, Any]]:
        path = self._path(user_id)
        if not path.is_file():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        rows = raw.get("messages") if isinstance(raw, dict) else raw
        out: list[dict[str, Any]] = []
        for item in rows or []:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            text = item.get("text")
            if role not in {"user", "assistant", "system"} or not isinstance(text, str):
                continue
            out.append(
                {
                    "role": role,
                    "text": text,
                    "ts": float(item.get("ts") or 0),
                }
            )
        return out[-self.max_messages :]

    def save(self, user_id: str, messages: list[dict[str, Any]]) -> None:
        trimmed = messages[-self.max_messages :]
        payload = {"messages": trimmed, "updated_at": time.time()}
        self._path(user_id).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def append(
        self,
        user_id: str,
        role: Role,
        text: str,
    ) -> list[dict[str, Any]]:
        rows = self.load(user_id)
        rows.append({"role": role, "text": text, "ts": time.time()})
        self.save(user_id, rows)
        return rows

    def clear(self, user_id: str) -> None:
        path = self._path(user_id)
        if path.is_file():
            path.unlink()

    @staticmethod
    def llm_turns(messages: list[dict[str, Any]], *, max_turns: int = 40) -> list[dict[str, str]]:
        """User/assistant pairs for seeding the agent after a hub restart."""
        turns: list[dict[str, str]] = []
        for item in messages:
            role = item.get("role")
            text = str(item.get("text") or "").strip()
            if role in {"user", "assistant"} and text:
                turns.append({"role": str(role), "content": text})
        return turns[-max_turns:]
