"""Telegram long-polling interface — control the PC assistant from a phone."""

from __future__ import annotations

import threading
import time
from typing import Any

from core.agent import Agent
from core.config import Settings
from core.confirmation import TelegramGateway, use_confirmation_gateway
from core.inbound import extract_media, save_telegram_media, transcribe_audio_file
from core.pairing import PairingStore
from core.registry import ToolRegistry
from core.telegram_api import TelegramAPIError, TelegramClient


HELP_TEXT = """\
Nexus Node — remote PC assistant

Send a text command, or drop a file / photo / voice note — it will be saved on the PC.

Commands:
/start       — welcome
/help        — this help
/pair CODE   — link this chat (code is printed on the PC console)
/tools       — list available tools
/reset       — clear conversation memory
/status      — PC online / config check
/inbox       — show inbound save folder

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
        pairing: PairingStore | None = None,
    ) -> None:
        if not settings.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required for Telegram mode")

        self.settings = settings
        self.registry = registry
        self.client = client or TelegramClient(settings.telegram_bot_token)
        self.pairing = pairing or PairingStore(
            data_dir=settings.data_dir,
            env_pair_code=settings.telegram_pair_code,
            seed_chat_ids=settings.allowed_telegram_chat_ids,
        )
        self._agents: dict[str, Agent] = {}
        self._gateways: dict[str, TelegramGateway] = (
            shared_gateways if shared_gateways is not None else {}
        )
        self._stop = threading.Event()
        self._offset: int | None = None

    def stop(self) -> None:
        self._stop.set()

    def run_in_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.run_forever, name="telegram-bot", daemon=True)
        thread.start()
        return thread

    def run_forever(self) -> None:
        """Block and process updates until stop() or KeyboardInterrupt."""
        print("Telegram bot polling…")
        print(f"Pair code: {self.pairing.pair_code}")
        print(f"Paired chats: {sorted(self.pairing.allowed) or '(none yet — send /pair CODE)'}")
        print(f"Inbox: {self.settings.inbox_dir}")
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
        return self.pairing.is_allowed(chat_id)

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
        if chat_id is None:
            return

        text = (message.get("text") or message.get("caption") or "").strip()
        media = extract_media(message)

        # Pairing is allowed before allowlist membership.
        if text.lower().startswith("/pair"):
            self._handle_pair(chat_id, text)
            return

        if not self._chat_allowed(chat_id):
            self.client.send_message(
                chat_id,
                "This chat is not paired.\n"
                "On the PC console, find the pair code, then send:\n"
                "/pair YOURCODE",
            )
            return

        if media is not None:
            self._handle_inbound_media(chat_id, media, caption=text)
            return

        if not text:
            return

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
            self._send_status(chat_id)
            return
        if lowered in {"/inbox", "inbox"}:
            self.client.send_message(
                chat_id,
                f"Inbound folder on PC:\n{self.settings.inbox_dir.resolve()}",
            )
            return

        if text.startswith("/"):
            self.client.send_message(chat_id, "Unknown command. Try /help.")
            return

        self._run_agent(chat_id, text)

    def _handle_pair(self, chat_id: int | str, text: str) -> None:
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            self.client.send_message(
                chat_id,
                "Usage: /pair CODE\nThe code is printed on the PC when the bot starts.",
            )
            return
        code = parts[1].strip()
        if self.pairing.try_pair(chat_id, code):
            print(f"Paired Telegram chat: {chat_id}")
            self.client.send_message(
                chat_id,
                f"Paired ✅ chat {chat_id}\nYou can now send commands, files, and voice notes.",
            )
            self.client.send_message(chat_id, HELP_TEXT)
        else:
            self.client.send_message(chat_id, "Invalid pair code. Check the PC console and retry.")

    def _send_status(self, chat_id: int | str) -> None:
        s = self.settings
        self.client.send_message(
            chat_id,
            "\n".join(
                [
                    "PC online ✅",
                    f"OpenAI: {'yes' if s.has_openai else 'NO'}",
                    f"Model: {s.openai_model}",
                    f"Tools: {len(self.registry)}",
                    f"Chat: {chat_id}",
                    f"Paired chats: {len(self.pairing.allowed)}",
                    f"Inbox: {s.inbox_dir}",
                ]
            ),
        )

    def _handle_inbound_media(
        self,
        chat_id: int | str,
        media: dict[str, Any],
        *,
        caption: str,
    ) -> None:
        self.client.send_message(chat_id, f"📥 Saving {media.get('kind')} to PC…")
        saved = save_telegram_media(
            self.client,
            media,
            inbox_dir=self.settings.inbox_dir,
            chat_id=chat_id,
        )
        if not saved.get("ok"):
            self.client.send_message(chat_id, f"Failed to save media: {saved.get('error')}")
            return

        lines = [
            f"Saved ✅ ({saved.get('kind')})",
            f"Path: {saved.get('path')}",
            f"Size: {saved.get('size_bytes')} bytes",
        ]

        transcript = None
        if saved.get("kind") in {"voice", "audio"}:
            tr = transcribe_audio_file(
                saved["path"],
                api_key=self.settings.openai_api_key,
            )
            if tr.get("ok"):
                transcript = tr.get("text") or ""
                lines.append(f"Transcript: {transcript}")
            elif tr.get("skipped"):
                lines.append(str(tr.get("error")))
            else:
                lines.append(f"Transcription failed: {tr.get('error')}")

        self.client.send_message(chat_id, "\n".join(lines))

        # If the user attached a caption or we transcribed voice, run the agent.
        follow_up = caption.strip()
        if not follow_up and transcript:
            follow_up = transcript
        if follow_up:
            # Tell the agent where the file landed so tools can use the path.
            prompt = (
                f"{follow_up}\n\n"
                f"[Attached file saved on PC at: {saved.get('path')} "
                f"(kind={saved.get('kind')})]"
            )
            self._run_agent(chat_id, prompt)

    def _run_agent(self, chat_id: int | str, text: str) -> None:
        agent = self._agent_for(chat_id)
        gateway = self.gateway_for(chat_id)
        self.client.send_message(chat_id, "⏳ Working…")

        with use_confirmation_gateway(gateway):
            try:
                reply = agent.run(text)
            except Exception as exc:  # noqa: BLE001
                reply = f"Error while running agent: {exc}"

        self.client.send_message(chat_id, reply or "(empty reply)")
