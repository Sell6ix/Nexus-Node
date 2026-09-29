"""Per-Telegram-user profiles: BYOK API keys and preferences."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{10,}|Bearer\s+\S+)", re.I)


@dataclass
class UserProfile:
    chat_id: str
    api_key: str | None = None
    api_base_url: str | None = None  # OpenAI-compatible endpoint
    model: str | None = None
    active_device_id: str | None = None
    display_name: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def has_api_key(self) -> bool:
        return bool(self.api_key)

    def public_dict(self) -> dict[str, Any]:
        """Safe view for status messages (never includes raw key)."""
        key = self.api_key or ""
        masked = ""
        if key:
            masked = key[:7] + "…" + key[-4:] if len(key) > 12 else "***"
        return {
            "chat_id": self.chat_id,
            "api_key": masked or "(not set)",
            "api_base_url": self.api_base_url or "(default OpenAI)",
            "model": self.model or "(default)",
            "active_device_id": self.active_device_id or "(local)",
            "display_name": self.display_name,
        }


class UserStore:
    """JSON profiles under ``data/users/<chat_id>.json``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, chat_id: int | str) -> Path:
        safe = re.sub(r"[^\w\-]", "_", str(chat_id))
        return self.root / f"{safe}.json"

    def get(self, chat_id: int | str) -> UserProfile:
        path = self._path(chat_id)
        if not path.is_file():
            return UserProfile(chat_id=str(chat_id))
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return UserProfile(chat_id=str(chat_id))
        return UserProfile(
            chat_id=str(chat_id),
            api_key=raw.get("api_key") or None,
            api_base_url=raw.get("api_base_url") or None,
            model=raw.get("model") or None,
            active_device_id=raw.get("active_device_id") or None,
            display_name=str(raw.get("display_name") or ""),
            extra=dict(raw.get("extra") or {}),
        )

    def save(self, profile: UserProfile) -> None:
        path = self._path(profile.chat_id)
        payload = asdict(profile)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def set_api_key(self, chat_id: int | str, api_key: str | None) -> UserProfile:
        profile = self.get(chat_id)
        profile.api_key = (api_key or "").strip() or None
        self.save(profile)
        return profile

    def set_base_url(self, chat_id: int | str, base_url: str | None) -> UserProfile:
        profile = self.get(chat_id)
        value = (base_url or "").strip().rstrip("/") or None
        profile.api_base_url = value
        self.save(profile)
        return profile

    def set_model(self, chat_id: int | str, model: str | None) -> UserProfile:
        profile = self.get(chat_id)
        profile.model = (model or "").strip() or None
        self.save(profile)
        return profile

    def set_active_device(self, chat_id: int | str, device_id: str | None) -> UserProfile:
        profile = self.get(chat_id)
        profile.active_device_id = (device_id or "").strip() or None
        self.save(profile)
        return profile


def redact_secrets(text: str) -> str:
    return _SECRET_RE.sub("***", text or "")
