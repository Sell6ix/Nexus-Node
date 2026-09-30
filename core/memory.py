"""Long-term personal memory facts per user (injected into the agent prompt)."""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any


class MemoryStore:
    """JSON memory under ``data/memory/<user_id>.json``."""

    def __init__(self, root: Path, *, max_facts: int = 80) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_facts = max(10, int(max_facts))

    def _path(self, user_id: str) -> Path:
        safe = re.sub(r"[^\w\-]", "_", str(user_id)) or "user"
        return self.root / f"{safe}.json"

    def list(self, user_id: str) -> list[dict[str, Any]]:
        path = self._path(user_id)
        if not path.is_file():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        rows = raw.get("facts") if isinstance(raw, dict) else raw
        out: list[dict[str, Any]] = []
        for item in rows or []:
            if isinstance(item, dict) and str(item.get("text") or "").strip():
                out.append(item)
        return out[-self.max_facts :]

    def save(self, user_id: str, facts: list[dict[str, Any]]) -> None:
        payload = {"facts": facts[-self.max_facts :], "updated_at": time.time()}
        self._path(user_id).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def add(self, user_id: str, text: str) -> dict[str, Any]:
        body = text.strip()
        if not body:
            raise ValueError("text must not be empty")
        facts = self.list(user_id)
        # de-dupe exact (case-insensitive)
        for f in facts:
            if str(f.get("text") or "").strip().lower() == body.lower():
                return f
        fact = {
            "id": uuid.uuid4().hex[:10],
            "text": body,
            "created_at": time.time(),
        }
        facts.append(fact)
        self.save(user_id, facts)
        return fact

    def remove(self, user_id: str, id_or_query: str) -> dict[str, Any] | None:
        q = (id_or_query or "").strip().lower()
        if not q:
            return None
        facts = self.list(user_id)
        for i, f in enumerate(facts):
            if str(f.get("id") or "") == id_or_query.strip():
                removed = facts.pop(i)
                self.save(user_id, facts)
                return removed
        hits = [f for f in facts if q in str(f.get("text") or "").lower()]
        if len(hits) == 1:
            facts = [f for f in facts if f.get("id") != hits[0].get("id")]
            self.save(user_id, facts)
            return hits[0]
        return None

    def search(self, user_id: str, query: str) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        rows = self.list(user_id)
        if not q:
            return rows
        return [f for f in rows if q in str(f.get("text") or "").lower()]

    def prompt_block(self, user_id: str, *, max_chars: int = 2500) -> str:
        facts = self.list(user_id)
        if not facts:
            return ""
        lines = ["Known facts about this user (keep private; use when helpful):"]
        used = len(lines[0])
        for f in facts:
            line = f"- {str(f.get('text') or '').strip()}"
            if used + len(line) + 1 > max_chars:
                lines.append("- …(more facts stored; use recall_memory/list_memory)")
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)
