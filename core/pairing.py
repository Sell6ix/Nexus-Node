"""Telegram chat pairing — owner + friends, invites, rate limits."""

from __future__ import annotations

import json
import secrets
import string
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from core.permissions import Role


DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ALLOWED_CHATS_FILE = "allowed_chats.json"
PAIR_CODE_FILE = "pair_code.txt"
DEFAULT_CODE_LENGTH = 10
DEFAULT_MAX_FRIENDS = 3
INVITE_TTL_SECONDS = 30 * 60


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
    FRIEND_LIMIT = "friend_limit"
    INVITE_REQUIRED = "invite_required"


@dataclass
class AttemptWindow:
    failures: int = 0
    window_start: float = 0.0
    locked_until: float = 0.0


@dataclass
class Invite:
    code: str
    created_at: float
    expires_at: float
    created_by: str
    label: str = ""


class PairingStore:
    """Owner-centric allowlist with optional one-time friend invites."""

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
        max_friends: int = DEFAULT_MAX_FRIENDS,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.allowed_path = self.data_dir / ALLOWED_CHATS_FILE
        self.code_path = self.data_dir / PAIR_CODE_FILE
        self.allowed: set[str] = set()
        self.friends: set[str] = set()
        self.owner_chat_id: str | None = None
        self.pairing_open: bool = True
        self.max_pair_attempts = max(1, max_pair_attempts)
        self.pair_lockout_seconds = max(30.0, pair_lockout_seconds)
        self.code_length = max(8, code_length)
        self.max_friends = max(0, max_friends)
        self._attempts: dict[str, AttemptWindow] = {}
        self._invites: dict[str, Invite] = {}
        self.fixed_owner = str(owner_chat_id) if owner_chat_id else None

        self.pair_code = (env_pair_code or "").strip().upper()
        self._load_state()

        if self.fixed_owner:
            self.owner_chat_id = self.fixed_owner
            self.allowed.add(self.fixed_owner)
            self.friends.discard(self.fixed_owner)

        if seed_chat_ids:
            for cid in seed_chat_ids:
                cid_s = str(cid)
                self.allowed.add(cid_s)
                if self.owner_chat_id and cid_s != str(self.owner_chat_id):
                    self.friends.add(cid_s)

        if self.owner_chat_id is None and self.allowed:
            self.owner_chat_id = sorted(self.allowed)[0]
            self.friends = {c for c in self.allowed if c != self.owner_chat_id}

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
        friends = raw.get("friend_chat_ids")
        if isinstance(friends, list):
            self.friends = {str(x) for x in friends if str(x) != self.owner_chat_id}
        else:
            self.friends = {c for c in self.allowed if c != self.owner_chat_id}
        if "pairing_open" in raw:
            self.pairing_open = bool(raw.get("pairing_open"))

    def _save_state(self) -> None:
        if self.owner_chat_id:
            self.allowed.add(str(self.owner_chat_id))
            self.friends.discard(str(self.owner_chat_id))
        payload = {
            "owner_chat_id": self.owner_chat_id,
            "pairing_open": self.pairing_open,
            "chat_ids": sorted(self.allowed),
            "friend_chat_ids": sorted(self.friends),
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

    def role_of(self, chat_id: int | str) -> Role:
        key = str(chat_id)
        if self.owner_chat_id and key == str(self.owner_chat_id):
            return Role.OWNER
        if key in self.allowed:
            return Role.FRIEND
        return Role.STRANGER

    def is_allowed(self, chat_id: int | str) -> bool:
        return str(chat_id) in self.allowed

    def is_owner(self, chat_id: int | str) -> bool:
        return self.role_of(chat_id) == Role.OWNER

    def is_friend(self, chat_id: int | str) -> bool:
        return self.role_of(chat_id) == Role.FRIEND

    def has_any_paired(self) -> bool:
        return bool(self.allowed)

    def set_pairing_open(self, open_: bool) -> None:
        self.pairing_open = bool(open_)
        self._save_state()

    def create_invite(
        self,
        created_by: int | str,
        *,
        label: str = "",
        ttl_seconds: float = INVITE_TTL_SECONDS,
    ) -> Invite | None:
        """Owner-only one-time invite for a friend. Returns None if friend cap reached."""
        if not self.is_owner(created_by):
            return None
        if len(self.friends) >= self.max_friends:
            return None
        self._purge_invites()
        code = generate_pair_code(self.code_length)
        now = time.time()
        invite = Invite(
            code=code,
            created_at=now,
            expires_at=now + max(60.0, ttl_seconds),
            created_by=str(created_by),
            label=(label or "").strip()[:40],
        )
        self._invites[code] = invite
        return invite

    def _purge_invites(self) -> None:
        now = time.time()
        expired = [c for c, inv in self._invites.items() if inv.expires_at <= now]
        for code in expired:
            self._invites.pop(code, None)

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

        normalized = code.strip().upper()
        self._purge_invites()

        # Prefer one-time invite codes (friend path).
        invite = self._invites.get(normalized)
        if invite is not None:
            if invite.expires_at <= time.time():
                self._invites.pop(normalized, None)
                self._register_failure(key)
                return PairResult.BAD_CODE
            if len(self.friends) >= self.max_friends:
                self._register_failure(key)
                return PairResult.FRIEND_LIMIT
            if not self.owner_chat_id:
                self._register_failure(key)
                return PairResult.LOCKED
            self.allowed.add(key)
            self.friends.add(key)
            self._invites.pop(normalized, None)
            self._attempts.pop(key, None)
            self._save_state()
            return PairResult.OK

        # Host console pair code — used to claim ownership (first) or when pairing_open.
        if self.fixed_owner and key != self.fixed_owner and not self.pairing_open:
            self._register_failure(key)
            return PairResult.WRONG_CHAT

        if not self.pairing_open and self.owner_chat_id is not None:
            # Friends must use /invite codes once an owner exists.
            self._register_failure(key)
            return PairResult.INVITE_REQUIRED

        if normalized != self.pair_code:
            self._register_failure(key)
            return PairResult.BAD_CODE

        first_owner = self.owner_chat_id is None
        if first_owner:
            self.allowed.add(key)
            self.owner_chat_id = key
            self.pairing_open = False
            self._attempts.pop(key, None)
            self.rotate_code()
            self._save_state()
            return PairResult.OK

        # pairing_open path for extra friends via host code (legacy/unlock).
        if len(self.friends) >= self.max_friends:
            self._register_failure(key)
            return PairResult.FRIEND_LIMIT
        self.allowed.add(key)
        self.friends.add(key)
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
        self.friends.discard(key)
        self._save_state()
        return True

    def rotate_code(self) -> str:
        self.pair_code = generate_pair_code(self.code_length)
        self.code_path.write_text(self.pair_code + "\n", encoding="utf-8")
        return self.pair_code

    def list_members(self) -> dict[str, list[str]]:
        return {
            "owner": [self.owner_chat_id] if self.owner_chat_id else [],
            "friends": sorted(self.friends),
        }
