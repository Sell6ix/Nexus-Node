"""Minimal Telegram Bot API client (stdlib only — no extra deps)."""

from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class TelegramAPIError(RuntimeError):
    """Raised when the Telegram Bot API returns an error."""


class TelegramClient:
    """Thin wrapper around ``https://api.telegram.org/bot<token>/...``."""

    def __init__(self, bot_token: str, *, timeout: float = 60.0) -> None:
        if not bot_token:
            raise ValueError("bot_token is required")
        self.bot_token = bot_token
        self.timeout = timeout
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def call(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call a Bot API method with a JSON body."""
        data = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/{method}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TelegramAPIError(f"{method} HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise TelegramAPIError(f"{method} network error: {exc}") from exc

        parsed = json.loads(raw)
        if not parsed.get("ok"):
            raise TelegramAPIError(f"{method} failed: {parsed}")
        return parsed.get("result")

    def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 25,
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates
        # Long poll needs a longer socket timeout than the poll timeout.
        old = self.timeout
        self.timeout = max(old, float(timeout) + 10.0)
        try:
            result = self.call("getUpdates", payload)
        finally:
            self.timeout = old
        return result if isinstance(result, list) else []

    def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": _truncate(text, 4096),
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return self.call("sendMessage", payload)

    def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = _truncate(text, 200)
        return self.call("answerCallbackQuery", payload)

    def edit_message_reply_markup(
        self,
        chat_id: int | str,
        message_id: int,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.call("editMessageReplyMarkup", payload)

    def delete_message(self, chat_id: int | str, message_id: int) -> dict[str, Any]:
        """Best-effort delete (used to scrub /apikey messages from chat history)."""
        return self.call(
            "deleteMessage",
            {"chat_id": chat_id, "message_id": message_id},
        )

    def send_document(self, chat_id: int | str, file_path: str | Path) -> dict[str, Any]:
        """Upload a local file as a document (multipart/form-data)."""
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        boundary = "----NexusNodeBoundary7MA4YWxkTrZu0gW"
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        file_bytes = path.read_bytes()
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
            f"{chat_id}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="document"; filename="{path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

        request = urllib.request.Request(
            f"{self.base_url}/sendDocument",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TelegramAPIError(f"sendDocument HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise TelegramAPIError(f"sendDocument network error: {exc}") from exc

        parsed = json.loads(raw)
        if not parsed.get("ok"):
            raise TelegramAPIError(f"sendDocument failed: {parsed}")
        return parsed.get("result")

    def get_file(self, file_id: str) -> dict[str, Any]:
        return self.call("getFile", {"file_id": file_id})

    def download_file(self, file_path: str, dest: Path) -> Path:
        """Download a Bot API ``file_path`` (from getFile) to ``dest``."""
        url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                dest.write_bytes(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TelegramAPIError(f"download_file HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise TelegramAPIError(f"download_file network error: {exc}") from exc
        return dest


def inline_confirm_keyboard(request_id: str) -> dict[str, Any]:
    """Build Yes/No inline keyboard for a confirmation request."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Yes", "callback_data": f"confirm:{request_id}:yes"},
                {"text": "❌ No", "callback_data": f"confirm:{request_id}:no"},
            ]
        ]
    }


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n…(truncated)"
