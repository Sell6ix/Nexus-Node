"""Telegram chat pairing — owner-locked allowlist with rate limits."""

from __future__ import annotations

import json
import secrets
import string
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ALLOWED_CHATS_FILE = "allowed_chats.json"
PAIR_CODE_FILE = "pair_code.txt"
DEFAULT_CODE_LENGTH = 10


def generate_pair_code(length: int = DEFAULT_CODE_LENGTH) -> str:
    alphabet = string.ascii_uppercase + string.digits
    alphabet = alphabet.replace("O", "").replace("0", "").replace("I", "").replace("1", "")
    return "".join(secrets.choice(alphabet) for _ in range(length))


class PairResult(str, Enum):
    OK = "ok"
    BAD_CODE = "bad_code"
    LOCKED = "locked"
    RATE_LIMITED = "rate_limited"
    WRONG_CHAT = "wrong_chat"
    ALREADY_PAIRED = "already_paired"


@dataclass
class AttemptWindow:
    failures: int = 0
    window_start: float = 0.0
    locked_until: float = 0.0


class PairingStore:
    """Owner-centric allowlist. Strangers cannot drive the PC without the host code."""

    def __init__(
        self,
        data_dir: Path | None = None,
        *,
        env_pair_code: str | None = None,
        seed_chat_ids: set[str] | None = None,
        owner_chat_id: str | None = None,
        pairing_open: bool | None = None,
        max_pair_attempts: int = 5,
        pair_lockout_seconds: float = 900.0,
        code_length: int = DEFAULT_CODE_LENGTH,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.allowed_path = self.data_dir / ALLOWED_CHATS_FILE
        self.code_path = self.data_dir / PAIR_CODE_FILE
        self.allowed: set[str] = set()
        self.owner_chat_id: str | None = None
        # Open only until the first owner is established (unless env forces otherwise).
        self.pairing_open: bool = True
        self.max_pair_attempts = max(1, max_pair_attempts)
        self.pair_lockout_seconds = max(30.0, pair_lockout_seconds)
        self.code_length = max(8, code_length)
        self._attempts: dict[str, AttemptWindow] = {}
        self.fixed_owner = str(owner_chat_id) if owner_chat_id else None

        self.pair_code = (env_pair_code or "").strip().upper()
        self._load_state()

        if self.fixed_owner:
            self.owner_chat_id = self.fixed_owner
            self.allowed.add(self.fixed_owner)

        if seed_chat_ids:
            self.allowed |= {str(x) for x in seed_chat_ids}

        if self.owner_chat_id is None and self.allowed:
            self.owner_chat_id = sorted(self.allowed)[0]

        if pairing_open is not None:
            self.pairing_open = pairing_open
        elif self.owner_chat_id is not None:
            self.pairing_open = False

        self._save_state()

        if not self.pair_code:
            self.pair_code = self._load_or_create_code()
        else:
            self.code_path.write_text(self.pair_code + "\n", encoding="utf-8")

    def _load_state(self) -> None:
        if not self.allowed_path.is_file():
            return
        try:
            raw = json.loads(self.allowed_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return
        if isinstance(raw, list):
            self.allowed = {str(x) for x in raw}
            return
        if not isinstance(raw, dict):
            return
        self.allowed = {str(x) for x in raw.get("chat_ids", [])}
        owner = raw.get("owner_chat_id")
        if owner:
            self.owner_chat_id = str(owner)
            self.allowed.add(self.owner_chat_id)
        if "pairing_open" in raw:
            self.pairing_open = bool(raw.get("pairing_open"))

    def _save_state(self) -> None:
        payload = {
            "owner_chat_id": self.owner_chat_id,
            "pairing_open": self.pairing_open,
            "chat_ids": sorted(self.allowed),
        }
        self.allowed_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _load_or_create_code(self) -> str:
        if self.code_path.is_file():
            existing = self.code_path.read_text(encoding="utf-8").strip().upper()
            if existing:
                return existing
        code = generate_pair_code(self.code_length)
        self.code_path.write_text(code + "\n", encoding="utf-8")
        return code

    def is_allowed(self, chat_id: int | str) -> bool:
        return str(chat_id) in self.allowed

    def is_owner(self, chat_id: int | str) -> bool:
        return self.owner_chat_id is not None and str(chat_id) == str(self.owner_chat_id)

    def has_any_paired(self) -> bool:
        return bool(self.allowed)

    def set_pairing_open(self, open_: bool) -> None:
        self.pairing_open = bool(open_)
        self._save_state()

    def _rate_limited(self, chat_id: str) -> bool:
        now = time.time()
        window = self._attempts.get(chat_id)
        if window is None:
            return False
        if window.locked_until and now < window.locked_until:
            return True
        if window.locked_until and now >= window.locked_until:
            self._attempts[chat_id] = AttemptWindow()
        return False

    def _register_failure(self, chat_id: str) -> None:
        now = time.time()
        window = self._attempts.get(chat_id) or AttemptWindow(window_start=now)
        if now - window.window_start > 600:
            window = AttemptWindow(window_start=now)
        window.failures += 1
        if window.failures >= self.max_pair_attempts:
            window.locked_until = now + self.pair_lockout_seconds
        self._attempts[chat_id] = window

    def try_pair(self, chat_id: int | str, code: str) -> PairResult:
        key = str(chat_id)
        if key in self.allowed:
            return PairResult.ALREADY_PAIRED
        if self._rate_limited(key):
            return PairResult.RATE_LIMITED

        # Fixed owner from env: only that chat may ever claim ownership via /pair.
        if self.fixed_owner and key != self.fixed_owner and not self.pairing_open:
            self._register_failure(key)
            return PairResult.WRONG_CHAT

        # Pairing locked: reject new chats (owner already established).
        if not self.pairing_open and self.owner_chat_id is not None:
            self._register_failure(key)
            return PairResult.LOCKED

        if code.strip().upper() != self.pair_code:
            self._register_failure(key)
            return PairResult.BAD_CODE

        # Success — bind chat.
        first_owner = self.owner_chat_id is None
        self.allowed.add(key)
        if first_owner:
            self.owner_chat_id = key
            # Fail closed: first successful pair becomes owner and locks the door.
            self.pairing_open = False
        self._attempts.pop(key, None)
        self.rotate_code()
        self._save_state()
        return PairResult.OK

    def revoke(self, chat_id: int | str) -> bool:
        key = str(chat_id)
        if self.owner_chat_id and key == str(self.owner_chat_id):
            return False
        if key not in self.allowed:
            return False
        self.allowed.discard(key)
        self._save_state()
        return True

    def rotate_code(self) -> str:
        self.pair_code = generate_pair_code(self.code_length)
        self.code_path.write_text(self.pair_code + "\n", encoding="utf-8")
        return self.pair_code
