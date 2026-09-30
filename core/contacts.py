"""Per-user address book for messaging tools."""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any


class ContactStore:
    """JSON contacts under ``data/contacts/<user_id>.json``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

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
        rows = raw.get("contacts") if isinstance(raw, dict) else raw
        out: list[dict[str, Any]] = []
        for item in rows or []:
            if isinstance(item, dict) and item.get("name"):
                out.append(item)
        return sorted(out, key=lambda c: str(c.get("name") or "").lower())

    def save(self, user_id: str, contacts: list[dict[str, Any]]) -> None:
        payload = {"contacts": contacts, "updated_at": time.time()}
        self._path(user_id).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def find(self, user_id: str, query: str) -> dict[str, Any] | None:
        q = (query or "").strip().lower()
        if not q:
            return None
        for c in self.list(user_id):
            name = str(c.get("name") or "").strip().lower()
            email = str(c.get("email") or "").strip().lower()
            tg = str(c.get("telegram") or "").strip().lower()
            wa = re.sub(r"\D", "", str(c.get("whatsapp") or ""))
            q_digits = re.sub(r"\D", "", q)
            if q in {name, email, tg} or q == str(c.get("id") or "").lower():
                return c
            if q_digits and wa and q_digits == wa:
                return c
        # partial name match if unique
        hits = [
            c
            for c in self.list(user_id)
            if q in str(c.get("name") or "").lower()
        ]
        return hits[0] if len(hits) == 1 else None

    def upsert(
        self,
        user_id: str,
        *,
        name: str,
        email: str = "",
        telegram: str = "",
        whatsapp: str = "",
        notes: str = "",
        contact_id: str | None = None,
    ) -> dict[str, Any]:
        name_s = name.strip()
        if not name_s:
            raise ValueError("name is required")
        email_s = email.strip()
        telegram_s = telegram.strip().lstrip("@")
        whatsapp_s = re.sub(r"[^\d+]", "", whatsapp.strip())
        if whatsapp_s.startswith("+"):
            whatsapp_s = whatsapp_s[1:]
        whatsapp_s = re.sub(r"\D", "", whatsapp_s)
        if not email_s and not telegram_s and not whatsapp_s:
            raise ValueError("Provide at least an email, telegram, or whatsapp number")
        rows = self.list(user_id)
        existing = None
        if contact_id:
            existing = next((c for c in rows if c.get("id") == contact_id), None)
        if existing is None:
            existing = next(
                (c for c in rows if str(c.get("name") or "").lower() == name_s.lower()),
                None,
            )
        if existing is None:
            contact = {
                "id": uuid.uuid4().hex[:10],
                "name": name_s,
                "email": email_s or None,
                "telegram": telegram_s or None,
                "whatsapp": whatsapp_s or None,
                "notes": notes.strip() or None,
                "created_at": time.time(),
            }
            rows.append(contact)
        else:
            existing["name"] = name_s
            if email_s:
                existing["email"] = email_s
            if telegram_s:
                existing["telegram"] = telegram_s
            if whatsapp_s:
                existing["whatsapp"] = whatsapp_s
            if notes.strip():
                existing["notes"] = notes.strip()
            existing["updated_at"] = time.time()
            contact = existing
        self.save(user_id, rows)
        return contact

    def remove(self, user_id: str, contact_id: str) -> bool:
        rows = self.list(user_id)
        kept = [c for c in rows if c.get("id") != contact_id]
        if len(kept) == len(rows):
            # also allow delete by name
            kept = [
                c
                for c in rows
                if str(c.get("name") or "").lower() != contact_id.strip().lower()
            ]
        if len(kept) == len(rows):
            return False
        self.save(user_id, kept)
        return True
