"""Environment-based configuration with safe local fallbacks."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    openai_api_key: str | None
    openai_model: str
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    steamcmd_path: str
    adb_path: str
    cursor_cli_path: str

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


def load_settings() -> Settings:
    """Load settings from the environment. Missing secrets fall back to mock mode."""
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        steamcmd_path=os.getenv("STEAMCMD_PATH", "steamcmd"),
        adb_path=os.getenv("ADB_PATH", "adb"),
        cursor_cli_path=os.getenv("CURSOR_CLI_PATH", "cursor"),
    )
