"""Telegram long-polling interface — control the PC assistant from a phone."""

from __future__ import annotations

import threading
import time
from typing import Any

from core.agent import Agent
from core.config import Settings
from core.confirmation import TelegramGateway, use_confirmation_gateway
from core.registry import ToolRegistry
from core.telegram_api import TelegramAPIError, TelegramClient


HELP_TEXT = """\
Nexus Node — remote PC assistant

Send a normal message and I will run it on the host PC via tools.

Commands:
/start  — welcome
/help   — this help
/tools  — list available tools
/reset  — clear conversation memory
/status — config / readiness check

Dangerous actions ask for ✅ Yes / ❌ No buttons here in Telegram.
"""


class TelegramBot:
    """Long-poll Telegram updates and route them through the shared Agent."""

    def __init__(
        self,
        settings: Settings,
        registry: ToolRegistry,
        *,
        client: TelegramClient | None = None,
        shared_gateways: dict[str, TelegramGateway] | None = None,
    ) -> None:
        if not settings.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required for Telegram mode")

        self.settings = settings
        self.registry = registry
        self.client = client or TelegramClient(settings.telegram_bot_token)
        self.allowed_chat_ids = {str(x) for x in settings.allowed_telegram_chat_ids}
        self._agents: dict[str, Agent] = {}
        self._gateways: dict[str, TelegramGateway] = shared_gateways if shared_gateways is not None else {}
        self._stop = threading.Event()
        self._offset: int | None = None
        self._bound_first_chat = bool(self.allowed_chat_ids)

    def stop(self) -> None:
        self._stop.set()

    def run_in_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.run_forever, name="telegram-bot", daemon=True)
        thread.start()
        return thread

    def run_forever(self) -> None:
        """Block and process updates until stop() or KeyboardInterrupt."""
        print("Telegram bot polling…")
        allowed = sorted(self.allowed_chat_ids) or "(open — will bind to first chat that writes)"
        print(f"Allowed chats: {allowed}")
        print("Send a message to your bot from the phone or Telegram Desktop.\n")

        while not self._stop.is_set():
            try:
                updates = self.client.get_updates(
                    offset=self._offset,
                    timeout=25,
                    allowed_updates=["message", "callback_query"],
                )
            except TelegramAPIError as exc:
                print(f"getUpdates error: {exc}; retrying in 3s")
                time.sleep(3)
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"Unexpected poll error: {exc}; retrying in 3s")
                time.sleep(3)
                continue

            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    self._offset = update_id + 1
                try:
                    self._dispatch(update)
                except Exception as exc:  # noqa: BLE001
                    print(f"Failed to handle update: {exc}")

    def gateway_for(self, chat_id: int | str) -> TelegramGateway:
        key = str(chat_id)
        gateway = self._gateways.get(key)
        if gateway is None:
            gateway = TelegramGateway(
                client=self.client,
                chat_id=chat_id,
                timeout_seconds=self.settings.confirmation_timeout_seconds,
            )
            self._gateways[key] = gateway
        return gateway

    def _dispatch(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
            return
        message = update.get("message") or update.get("edited_message")
        if message:
            self._handle_message(message)

    def _chat_allowed(self, chat_id: int | str) -> bool:
        if not self.allowed_chat_ids:
            return True
        return str(chat_id) in self.allowed_chat_ids

    def _agent_for(self, chat_id: int | str) -> Agent:
        key = str(chat_id)
        agent = self._agents.get(key)
        if agent is None:
            gateway = self.gateway_for(chat_id)
            agent = Agent(
                self.registry,
                self.settings,
                confirm_fn=lambda prompt, _tg: gateway.ask(prompt),
            )
            self._agents[key] = agent
        return agent

    def _handle_callback(self, query: dict[str, Any]) -> None:
        data = query.get("data") or ""
        callback_id = query.get("id")
        message = query.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        message_id = message.get("message_id")

        if callback_id:
            try:
                self.client.answer_callback_query(callback_id)
            except TelegramAPIError:
                pass

        if chat_id is None or not self._chat_allowed(chat_id):
            return

        parts = data.split(":")
        if len(parts) != 3 or parts[0] != "confirm":
            return
        request_id, decision = parts[1], parts[2]
        allowed = decision == "yes"
        gateway = self.gateway_for(chat_id)
        found = gateway.resolve(request_id, allowed)

        if message_id is not None:
            try:
                self.client.edit_message_reply_markup(chat_id, int(message_id), reply_markup=None)
            except TelegramAPIError:
                pass

        status = "Approved ✅" if allowed else "Denied ❌"
        if not found:
            status = "Confirmation expired or already answered."
        try:
            self.client.send_message(chat_id, status)
        except TelegramAPIError:
            pass

    def _handle_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()
        if chat_id is None or not text:
            return

        if not self._chat_allowed(chat_id):
            self.client.send_message(
                chat_id,
                "Access denied. Add this chat id to TELEGRAM_ALLOWED_CHAT_IDS "
                "or TELEGRAM_CHAT_ID on the host.",
            )
            return

        if not self._bound_first_chat and not self.allowed_chat_ids:
            self.allowed_chat_ids = {str(chat_id)}
            self._bound_first_chat = True
            print(f"Bound Telegram allowlist to first chat: {chat_id}")

        lowered = text.lower()
        if lowered in {"/start", "start"}:
            self.client.send_message(chat_id, HELP_TEXT)
            return
        if lowered in {"/help", "help"}:
            self.client.send_message(chat_id, HELP_TEXT)
            return
        if lowered in {"/tools", "tools"}:
            lines = [f"• {t.name}" for t in self.registry.list_tools()]
            self.client.send_message(chat_id, "Registered tools:\n" + "\n".join(lines))
            return
        if lowered in {"/reset", "reset"}:
            self._agent_for(chat_id).reset()
            self.client.send_message(chat_id, "Conversation reset.")
            return
        if lowered in {"/status", "status"}:
            s = self.settings
            self.client.send_message(
                chat_id,
                "\n".join(
                    [
                        f"OpenAI: {'yes' if s.has_openai else 'NO'}",
                        f"Model: {s.openai_model}",
                        f"Tools: {len(self.registry)}",
                        f"Chat: {chat_id}",
                    ]
                ),
            )
            return

        if text.startswith("/"):
            self.client.send_message(chat_id, "Unknown command. Try /help.")
            return

        agent = self._agent_for(chat_id)
        gateway = self.gateway_for(chat_id)
        self.client.send_message(chat_id, "⏳ Working…")

        with use_confirmation_gateway(gateway):
            try:
                reply = agent.run(text)
            except Exception as exc:  # noqa: BLE001
                reply = f"Error while running agent: {exc}"

        self.client.send_message(chat_id, reply or "(empty reply)")
