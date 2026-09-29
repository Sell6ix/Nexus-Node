"""At-rest encryption for user API keys (Fernet)."""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "enc:v1:"


class SecretBox:
    """Encrypt short secrets. Key from ``NEXUS_SECRETS_KEY`` or ``data/secrets.key``."""

    def __init__(self, key_path: Path, *, env_key: str | None = None) -> None:
        self.key_path = Path(key_path)
        raw = (env_key if env_key is not None else os.getenv("NEXUS_SECRETS_KEY") or "").strip()
        if not raw:
            raw = self._load_or_create_file()
        self._fernet = Fernet(raw.encode("ascii") if isinstance(raw, str) else raw)

    def _load_or_create_file(self) -> str:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.is_file():
            value = self.key_path.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = Fernet.generate_key().decode("ascii")
        self.key_path.write_text(value + "\n", encoding="utf-8")
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return value

    def seal(self, plaintext: str | None) -> str | None:
        if not plaintext:
            return None
        token = self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return PREFIX + token

    def open(self, stored: str | None) -> str | None:
        if not stored:
            return None
        if not stored.startswith(PREFIX):
            return stored  # legacy plaintext
        token = stored[len(PREFIX) :].encode("ascii")
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except (InvalidToken, ValueError):
            return None


def default_box(data_dir: Path) -> SecretBox:
    return SecretBox(Path(data_dir) / "secrets.key")
