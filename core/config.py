"""Environment-based configuration with safe local fallbacks."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

from core.permissions import (
    DEFAULT_FRIEND_ALLOW_TOOLS,
    DEFAULT_FRIEND_DENY_TOOLS,
    PermissionPolicy,
    parse_tool_set,
)
from core.safety import (
    DEFAULT_SHELL_DENY_PATTERNS,
    SafetyPolicy,
    parse_csv_patterns,
)


def _split_ids(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _default_device_id() -> str:
    host = socket.gethostname().strip().lower() or "local"
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in host)
    return safe[:40] or "local"


@dataclass
class Settings:
    """Runtime settings loaded from environment variables."""

    openai_api_key: str | None
    openai_model: str
    openai_base_url: str | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    telegram_owner_chat_id: str | None = None
    telegram_allowed_chat_ids: set[str] = field(default_factory=set)
    telegram_pair_code: str | None = None
    telegram_pairing_open: bool | None = None
    pair_max_attempts: int = 5
    pair_lockout_seconds: float = 900.0
    max_friends: int = 5
    confirmation_timeout_seconds: float = 120.0
    inbox_dir: Path = field(default_factory=lambda: Path("data/inbox"))
    data_dir: Path = field(default_factory=lambda: Path("data"))
    audit_log_path: Path = field(default_factory=lambda: Path("data/audit.jsonl"))
    users_dir: Path = field(default_factory=lambda: Path("data/users"))
    devices_path: Path = field(default_factory=lambda: Path("data/devices.json"))
    device_id: str = "local"
    device_name: str = "Local PC"
    worker_host: str = "127.0.0.1"
    worker_port: int = 8765
    worker_token: str | None = None
    safety: SafetyPolicy = field(default_factory=SafetyPolicy)
    permissions: PermissionPolicy = field(default_factory=PermissionPolicy)
    steamcmd_path: str = "steamcmd"
    adb_path: str = "adb"
    cursor_cli_path: str = "cursor"

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def has_telegram_bot(self) -> bool:
        return bool(self.telegram_bot_token)

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def allowed_telegram_chat_ids(self) -> set[str]:
        ids = set(self.telegram_allowed_chat_ids)
        if self.telegram_chat_id:
            ids.add(str(self.telegram_chat_id))
        if self.telegram_owner_chat_id:
            ids.add(str(self.telegram_owner_chat_id))
        return ids

    @property
    def owner_chat_id(self) -> str | None:
        return self.telegram_owner_chat_id or self.telegram_chat_id


def load_settings() -> Settings:
    """Load settings from the environment. Missing secrets fall back to mock mode."""
    timeout_raw = os.getenv("CONFIRMATION_TIMEOUT_SECONDS", "120")
    try:
        timeout = float(timeout_raw)
    except ValueError:
        timeout = 120.0

    root = Path(__file__).resolve().parent.parent
    data_dir = Path(os.getenv("NEXUS_DATA_DIR", str(root / "data"))).expanduser()
    inbox_dir = Path(os.getenv("NEXUS_INBOX_DIR", str(data_dir / "inbox"))).expanduser()
    audit_log_path = Path(
        os.getenv("NEXUS_AUDIT_LOG", str(data_dir / "audit.jsonl"))
    ).expanduser()
    users_dir = Path(os.getenv("NEXUS_USERS_DIR", str(data_dir / "users"))).expanduser()
    devices_path = Path(
        os.getenv("NEXUS_DEVICES_PATH", str(data_dir / "devices.json"))
    ).expanduser()

    deny = parse_csv_patterns(
        os.getenv("SHELL_DENY_PATTERNS"),
        default=DEFAULT_SHELL_DENY_PATTERNS,
    )
    allow = parse_csv_patterns(os.getenv("SHELL_ALLOW_PREFIXES"), default=())

    safety = SafetyPolicy(
        shell_enabled=_env_bool("SHELL_ENABLED", True),
        shell_deny_patterns=deny,
        shell_allow_prefixes=allow,
        inbound_max_bytes=_env_int("INBOUND_MAX_BYTES", 25 * 1024 * 1024),
    )
    permissions = PermissionPolicy(
        friend_allow_tools=parse_tool_set(
            os.getenv("FRIEND_ALLOW_TOOLS"),
            default=DEFAULT_FRIEND_ALLOW_TOOLS,
        ),
        friend_deny_tools=parse_tool_set(
            os.getenv("FRIEND_DENY_TOOLS"),
            default=DEFAULT_FRIEND_DENY_TOOLS,
        ),
        friend_can_kill_process=_env_bool("FRIEND_CAN_KILL_PROCESS", False),
        notify_owner_on_friend_command=_env_bool("NOTIFY_OWNER_ON_FRIEND_COMMAND", True),
    )

    pairing_open_raw = os.getenv("TELEGRAM_PAIRING_OPEN")
    if pairing_open_raw is None or not pairing_open_raw.strip():
        pairing_open = None
    else:
        pairing_open = pairing_open_raw.strip().lower() in {"1", "true", "yes", "on"}

    device_id = os.getenv("DEVICE_ID") or _default_device_id()
    device_name = os.getenv("DEVICE_NAME") or socket.gethostname() or device_id

    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        openai_base_url=(os.getenv("OPENAI_BASE_URL") or None),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        telegram_owner_chat_id=os.getenv("TELEGRAM_OWNER_CHAT_ID") or None,
        telegram_allowed_chat_ids=_split_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS")),
        telegram_pair_code=(os.getenv("TELEGRAM_PAIR_CODE") or None),
        telegram_pairing_open=pairing_open,
        pair_max_attempts=_env_int("TELEGRAM_PAIR_MAX_ATTEMPTS", 5),
        pair_lockout_seconds=float(_env_int("TELEGRAM_PAIR_LOCKOUT_SECONDS", 900)),
        max_friends=_env_int("TELEGRAM_MAX_FRIENDS", 5),
        confirmation_timeout_seconds=timeout,
        inbox_dir=inbox_dir,
        data_dir=data_dir,
        audit_log_path=audit_log_path,
        users_dir=users_dir,
        devices_path=devices_path,
        device_id=device_id,
        device_name=device_name,
        worker_host=os.getenv("WORKER_HOST", "127.0.0.1"),
        worker_port=_env_int("WORKER_PORT", 8765),
        worker_token=os.getenv("WORKER_TOKEN") or os.getenv("DEVICE_TOKEN") or None,
        safety=safety,
        permissions=permissions,
        steamcmd_path=os.getenv("STEAMCMD_PATH", "steamcmd"),
        adb_path=os.getenv("ADB_PATH", "adb"),
        cursor_cli_path=os.getenv("CURSOR_CLI_PATH", "cursor"),
    )
