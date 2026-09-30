"""Per-user chat transcripts with multiple conversations."""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Literal

Role = Literal["user", "assistant", "system"]


class ChatHistory:
    """Conversations under ``data/chats/<user_id>/<conv_id>.json``."""

    def __init__(self, root: Path, *, max_messages: int = 200) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_messages = max(20, int(max_messages))

    def _safe(self, user_id: str) -> str:
        return re.sub(r"[^\w\-]", "_", str(user_id)) or "user"

    def _user_dir(self, user_id: str) -> Path:
        safe = self._safe(user_id)
        path = self.root / safe
        path.mkdir(parents=True, exist_ok=True)
        legacy = self.root / f"{safe}.json"
        main_path = path / "main.json"
        if legacy.is_file() and not main_path.is_file():
            try:
                legacy.replace(main_path)
            except OSError:
                pass
        index = path / "index.json"
        if not index.is_file():
            # Seed index from existing json files
            convs = []
            for f in sorted(path.glob("*.json")):
                if f.name == "index.json":
                    continue
                cid = f.stem
                title = "General" if cid == "main" else cid.replace("-", " ").title()
                convs.append(
                    {
                        "id": cid,
                        "title": title,
                        "created_at": f.stat().st_mtime,
                        "updated_at": f.stat().st_mtime,
                    }
                )
            if not convs:
                convs = [
                    {
                        "id": "main",
                        "title": "General",
                        "created_at": time.time(),
                        "updated_at": time.time(),
                    }
                ]
                main_path.write_text(
                    json.dumps({"messages": [], "updated_at": time.time()}, indent=2) + "\n",
                    encoding="utf-8",
                )
            index.write_text(
                json.dumps({"conversations": convs}, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        return path

    def _conv_path(self, user_id: str, conversation_id: str) -> Path:
        cid = re.sub(r"[^\w\-]", "_", conversation_id or "main") or "main"
        return self._user_dir(user_id) / f"{cid}.json"

    def _index_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "index.json"

    def list_conversations(self, user_id: str) -> list[dict[str, Any]]:
        self._user_dir(user_id)
        try:
            raw = json.loads(self._index_path(user_id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return [{"id": "main", "title": "General", "created_at": 0, "updated_at": 0}]
        rows = raw.get("conversations") if isinstance(raw, dict) else raw
        out = [c for c in (rows or []) if isinstance(c, dict) and c.get("id")]
        out.sort(key=lambda c: float(c.get("updated_at") or 0), reverse=True)
        return out

    def _save_index(self, user_id: str, conversations: list[dict[str, Any]]) -> None:
        self._index_path(user_id).write_text(
            json.dumps({"conversations": conversations}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def create_conversation(self, user_id: str, title: str = "") -> dict[str, Any]:
        self._user_dir(user_id)
        cid = uuid.uuid4().hex[:10]
        name = (title or "New chat").strip() or "New chat"
        conv = {
            "id": cid,
            "title": name[:80],
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        convs = self.list_conversations(user_id)
        convs.insert(0, conv)
        self._save_index(user_id, convs)
        self._conv_path(user_id, cid).write_text(
            json.dumps({"messages": [], "updated_at": time.time()}, indent=2) + "\n",
            encoding="utf-8",
        )
        return conv

    def rename_conversation(self, user_id: str, conversation_id: str, title: str) -> dict[str, Any] | None:
        convs = self.list_conversations(user_id)
        for c in convs:
            if c.get("id") == conversation_id:
                c["title"] = (title or c["title"]).strip()[:80]
                c["updated_at"] = time.time()
                self._save_index(user_id, convs)
                return c
        return None

    def delete_conversation(self, user_id: str, conversation_id: str) -> bool:
        if conversation_id == "main":
            self.clear(user_id, conversation_id)
            return True
        before = self.list_conversations(user_id)
        convs = [c for c in before if c.get("id") != conversation_id]
        if len(convs) == len(before):
            return False
        self._save_index(user_id, convs)
        path = self._conv_path(user_id, conversation_id)
        if path.is_file():
            path.unlink()
        return True

    def _touch(self, user_id: str, conversation_id: str) -> None:
        convs = self.list_conversations(user_id)
        found = False
        for c in convs:
            if c.get("id") == conversation_id:
                c["updated_at"] = time.time()
                found = True
                break
        if not found:
            convs.insert(
                0,
                {
                    "id": conversation_id,
                    "title": "General" if conversation_id == "main" else conversation_id,
                    "created_at": time.time(),
                    "updated_at": time.time(),
                },
            )
        self._save_index(user_id, convs)

    def load(self, user_id: str, conversation_id: str = "main") -> list[dict[str, Any]]:
        path = self._conv_path(user_id, conversation_id)
        if not path.is_file():
            # legacy single-file fallback
            legacy = self.root / f"{self._safe(user_id)}.json"
            if conversation_id == "main" and legacy.is_file():
                path = legacy
            else:
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

    def save(
        self,
        user_id: str,
        messages: list[dict[str, Any]],
        conversation_id: str = "main",
    ) -> None:
        trimmed = messages[-self.max_messages :]
        payload = {"messages": trimmed, "updated_at": time.time()}
        self._conv_path(user_id, conversation_id).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self._touch(user_id, conversation_id)

    def append(
        self,
        user_id: str,
        role: Role,
        text: str,
        conversation_id: str = "main",
    ) -> list[dict[str, Any]]:
        rows = self.load(user_id, conversation_id)
        rows.append({"role": role, "text": text, "ts": time.time()})
        self.save(user_id, rows, conversation_id)
        return rows

    def clear(self, user_id: str, conversation_id: str = "main") -> None:
        path = self._conv_path(user_id, conversation_id)
        if path.is_file():
            path.write_text(
                json.dumps({"messages": [], "updated_at": time.time()}, indent=2) + "\n",
                encoding="utf-8",
            )
        self._touch(user_id, conversation_id)

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
