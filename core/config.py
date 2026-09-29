"""Environment-based configuration with safe local fallbacks."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _split_ids(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


@dataclass
class Settings:
    """Runtime settings loaded from environment variables."""

    openai_api_key: str | None
    openai_model: str
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    telegram_allowed_chat_ids: set[str] = field(default_factory=set)
    telegram_pair_code: str | None = None
    confirmation_timeout_seconds: float = 120.0
    inbox_dir: Path = field(default_factory=lambda: Path("data/inbox"))
    data_dir: Path = field(default_factory=lambda: Path("data"))
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
        """True when backup/send targets are fully configured."""
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def allowed_telegram_chat_ids(self) -> set[str]:
        ids = set(self.telegram_allowed_chat_ids)
        if self.telegram_chat_id:
            ids.add(str(self.telegram_chat_id))
        return ids


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

    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        telegram_allowed_chat_ids=_split_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS")),
        telegram_pair_code=(os.getenv("TELEGRAM_PAIR_CODE") or None),
        confirmation_timeout_seconds=timeout,
        inbox_dir=inbox_dir,
        data_dir=data_dir,
        steamcmd_path=os.getenv("STEAMCMD_PATH", "steamcmd"),
        adb_path=os.getenv("ADB_PATH", "adb"),
        cursor_cli_path=os.getenv("CURSOR_CLI_PATH", "cursor"),
    )
