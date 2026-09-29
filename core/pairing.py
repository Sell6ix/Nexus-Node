"""Telegram chat pairing — code-gated allowlist persisted on disk."""

from __future__ import annotations

import json
import secrets
import string
from pathlib import Path


DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ALLOWED_CHATS_FILE = "allowed_chats.json"
PAIR_CODE_FILE = "pair_code.txt"


def generate_pair_code(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    # Avoid ambiguous characters.
    alphabet = alphabet.replace("O", "").replace("0", "").replace("I", "").replace("1", "")
    return "".join(secrets.choice(alphabet) for _ in range(length))


class PairingStore:
    """Manage pair code + allowed chat ids on local disk."""

    def __init__(
        self,
        data_dir: Path | None = None,
        *,
        env_pair_code: str | None = None,
        seed_chat_ids: set[str] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.allowed_path = self.data_dir / ALLOWED_CHATS_FILE
        self.code_path = self.data_dir / PAIR_CODE_FILE
        self.allowed: set[str] = set()
        self.pair_code = (env_pair_code or "").strip().upper()

        self._load_allowed()
        if seed_chat_ids:
            before = set(self.allowed)
            self.allowed |= {str(x) for x in seed_chat_ids}
            if self.allowed != before:
                self._save_allowed()

        if not self.pair_code:
            self.pair_code = self._load_or_create_code()
        else:
            self.code_path.write_text(self.pair_code + "\n", encoding="utf-8")

    def _load_allowed(self) -> None:
        if not self.allowed_path.is_file():
            return
        try:
            raw = json.loads(self.allowed_path.read_text(encoding="utf-8"))
            ids = raw.get("chat_ids", []) if isinstance(raw, dict) else raw
            self.allowed = {str(x) for x in ids}
        except (OSError, json.JSONDecodeError, TypeError):
            self.allowed = set()

    def _save_allowed(self) -> None:
        payload = {"chat_ids": sorted(self.allowed)}
        self.allowed_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _load_or_create_code(self) -> str:
        if self.code_path.is_file():
            existing = self.code_path.read_text(encoding="utf-8").strip().upper()
            if existing:
                return existing
        code = generate_pair_code()
        self.code_path.write_text(code + "\n", encoding="utf-8")
        return code

    def is_allowed(self, chat_id: int | str) -> bool:
        return str(chat_id) in self.allowed

    def has_any_paired(self) -> bool:
        return bool(self.allowed)

    def try_pair(self, chat_id: int | str, code: str) -> bool:
        if code.strip().upper() != self.pair_code:
            return False
        self.allowed.add(str(chat_id))
        self._save_allowed()
        return True

    def revoke(self, chat_id: int | str) -> None:
        self.allowed.discard(str(chat_id))
        self._save_allowed()

    def rotate_code(self) -> str:
        self.pair_code = generate_pair_code()
        self.code_path.write_text(self.pair_code + "\n", encoding="utf-8")
        return self.pair_code
