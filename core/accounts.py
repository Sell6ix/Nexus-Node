"""Multi-user accounts for the web app (password auth, roles, invites)."""

from __future__ import annotations

import json
import secrets
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import bcrypt

from core.secrets import SecretBox


RoleName = Literal["owner", "member"]


RoleName = Literal["owner", "member"]


@dataclass
class Account:
    id: str
    username: str
    password_hash: str
    role: RoleName = "member"
    display_name: str = ""
    created_at: float = field(default_factory=time.time)
    api_key: str | None = None
    api_base_url: str | None = None
    model: str | None = None
    active_device_id: str | None = None
    home_device_id: str | None = None
    enabled: bool = True

    def public_dict(self) -> dict[str, Any]:
        key = self.api_key or ""
        masked = ""
        if key:
            masked = key[:7] + "…" + key[-4:] if len(key) > 12 else "***"
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name or self.username,
            "role": self.role,
            "api_key": masked or None,
            "has_api_key": bool(self.api_key),
            "api_base_url": self.api_base_url,
            "model": self.model,
            "active_device_id": self.active_device_id,
            "home_device_id": self.home_device_id,
        }

    def resolve_device_id(self, fallback_local_id: str) -> str:
        return (
            (self.active_device_id or "").strip()
            or (self.home_device_id or "").strip()
            or fallback_local_id
        )


@dataclass
class Invite:
    code: str
    created_by: str
    label: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    used_by: str | None = None

    def is_valid(self) -> bool:
        if self.used_by:
            return False
        return time.time() < self.expires_at


class AccountStore:
    """JSON account registry under ``data/accounts.json``."""

    def __init__(
        self,
        path: Path,
        *,
        max_members: int = 50,
        secrets: SecretBox | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_members = max_members
        self.secrets = secrets
        self.accounts: dict[str, Account] = {}
        self.by_username: dict[str, str] = {}
        self.invites: dict[str, Invite] = {}
        self._state_lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in raw.get("accounts") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            acc = Account(
                id=str(item["id"]),
                username=str(item.get("username") or "").lower(),
                password_hash=str(item.get("password_hash") or ""),
                role=item.get("role") if item.get("role") in {"owner", "member"} else "member",
                display_name=str(item.get("display_name") or ""),
                created_at=float(item.get("created_at") or time.time()),
                api_key=self._unlock(item.get("api_key")),
                api_base_url=item.get("api_base_url") or None,
                model=item.get("model") or None,
                active_device_id=item.get("active_device_id") or None,
                home_device_id=item.get("home_device_id") or None,
                enabled=bool(item.get("enabled", True)),
            )
            if not acc.username:
                continue
            self.accounts[acc.id] = acc
            self.by_username[acc.username] = acc.id
        for item in raw.get("invites") or []:
            if not isinstance(item, dict) or not item.get("code"):
                continue
            inv = Invite(
                code=str(item["code"]),
                created_by=str(item.get("created_by") or ""),
                label=str(item.get("label") or ""),
                created_at=float(item.get("created_at") or time.time()),
                expires_at=float(item.get("expires_at") or 0),
                used_by=item.get("used_by"),
            )
            self.invites[inv.code] = inv
        # Rewrite plaintext keys as ciphertext after first load with a box.
        if self.secrets is not None:
            self._save()

    def _unlock(self, stored: Any) -> str | None:
        raw = stored if isinstance(stored, str) else None
        if not raw:
            return None
        if self.secrets is None:
            return raw
        return self.secrets.open(raw)

    def _lock(self, plaintext: str | None) -> str | None:
        if not plaintext:
            return None
        if self.secrets is None:
            return plaintext
        return self.secrets.seal(plaintext)

    def _save(self) -> None:
        rows = []
        for acc in self.accounts.values():
            payload = asdict(acc)
            payload["api_key"] = self._lock(acc.api_key)
            rows.append(payload)
        payload = {
            "accounts": rows,
            "invites": [asdict(i) for i in self.invites.values()],
        }
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    @property
    def has_owner(self) -> bool:
        return any(a.role == "owner" for a in self.accounts.values())

    def count_members(self) -> int:
        return sum(1 for a in self.accounts.values() if a.role == "member" and a.enabled)

    def get(self, account_id: str) -> Account | None:
        return self.accounts.get(account_id)

    def get_by_username(self, username: str) -> Account | None:
        aid = self.by_username.get(username.strip().lower())
        return self.accounts.get(aid) if aid else None

    def list_public(self) -> list[dict[str, Any]]:
        return [a.public_dict() for a in sorted(self.accounts.values(), key=lambda x: x.created_at)]

    @staticmethod
    def hash_password(password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        try:
            return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
        except ValueError:
            return False

    def register(
        self,
        username: str,
        password: str,
        *,
        display_name: str = "",
        invite_code: str | None = None,
        open_signup: bool = False,
    ) -> Account:
        with self._state_lock:
            user = (username or "").strip().lower()
            if not user or len(user) < 2:
                raise ValueError("Username must be at least 2 characters.")
            if not password or len(password) < 6:
                raise ValueError("Password must be at least 6 characters.")
            if user in self.by_username:
                raise ValueError("Username already taken.")

            role: RoleName = "member"
            invite: Invite | None = None
            if not self.has_owner:
                role = "owner"
            elif invite_code:
                invite = self.invites.get(invite_code.strip())
                if invite is None or not invite.is_valid():
                    raise ValueError("Invalid or expired invite code.")
                if self.count_members() >= self.max_members:
                    raise ValueError("Member limit reached.")
            elif open_signup:
                if self.count_members() >= self.max_members:
                    raise ValueError("Member limit reached.")
            else:
                raise ValueError("Registration requires an invite code from the owner.")

            acc = Account(
                id=uuid.uuid4().hex[:12],
                username=user,
                password_hash=self.hash_password(password),
                role=role,
                display_name=(display_name or user).strip(),
            )
            self.accounts[acc.id] = acc
            self.by_username[user] = acc.id
            if invite is not None:
                invite.used_by = acc.id
            self._save()
            return acc

    def authenticate(self, username: str, password: str) -> Account | None:
        acc = self.get_by_username(username)
        if acc is None or not acc.enabled:
            return None
        if not self.verify_password(password, acc.password_hash):
            return None
        return acc

    def save(self, account: Account) -> None:
        with self._state_lock:
            self.accounts[account.id] = account
            self.by_username[account.username] = account.id
            self._save()

    def create_invite(self, created_by: str, *, label: str = "", ttl_hours: float = 24.0) -> Invite:
        with self._state_lock:
            code = secrets.token_urlsafe(8)
            inv = Invite(
                code=code,
                created_by=created_by,
                label=label.strip(),
                expires_at=time.time() + ttl_hours * 3600,
            )
            self.invites[code] = inv
            self._save()
            return inv
