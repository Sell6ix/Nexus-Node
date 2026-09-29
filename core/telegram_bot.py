"""Telegram interface: multi-user BYOK, multi-device routing, owner security."""

from __future__ import annotations

import shlex
import threading
import time
from contextlib import nullcontext
from typing import Any

from core.agent import Agent
from core.audit import get_audit_logger
from core.config import Settings
from core.confirmation import TelegramGateway, use_confirmation_gateway
from core.context import request_context
from core.devices import DeviceRegistry, ping_device
from core.inbound import extract_media, save_telegram_media, transcribe_audio_file
from core.pairing import PairResult, PairingStore
from core.permissions import Role
from core.registry import ToolRegistry
from core.telegram_api import TelegramAPIError, TelegramClient
from core.users import UserStore, redact_secrets


HELP_TEXT = """\
Nexus Node — daily PC/phone assistant

Each person uses their own AI key. Commands can target different devices.

Setup (you):
/whoami
/apikey YOUR_KEY   ⚠️ then delete that message
/model gpt-4o-mini
/baseurl https://api.openai.com/v1   (optional, any OpenAI-compatible API)
/pair CODE   (first time — code is on the PC console)

Devices:
/devices
/use DEVICE_ID
/device_add ID "Name With Spaces" URL TOKEN   (owner, quotes OK)
/device_add ID|Name|URL|TOKEN                 (owner, pipe form)
/device_del ID                                (owner)

Friends:
/invite [label]   — one-time code for a friend (owner)
/friends
/lock /unlock /revoke ID

Other: /tools /status /inbox /audit /reset /help
"""

APIKEY_WARNING = (
    "⚠️ Security: your API key will appear in Telegram chat history "
    "(and possibly cloud backups). After sending, delete that message. "
    "Prefer a restricted/rotated key."
)


class TelegramBot:
    def __init__(
        self,
        settings: Settings,
        registry: ToolRegistry,
        *,
        client: TelegramClient | None = None,
        shared_gateways: dict[str, TelegramGateway] | None = None,
        pairing: PairingStore | None = None,
        users: UserStore | None = None,
        devices: DeviceRegistry | None = None,
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
            owner_chat_id=settings.owner_chat_id,
            pairing_open=settings.telegram_pairing_open,
            max_pair_attempts=settings.pair_max_attempts,
            pair_lockout_seconds=settings.pair_lockout_seconds,
            max_friends=settings.max_friends,
        )
        self.users = users or UserStore(settings.users_dir)
        self.devices = devices or DeviceRegistry(
            settings.devices_path,
            local_id=settings.device_id,
            local_name=settings.device_name,
            local_token=settings.worker_token,
        )
        self.audit = get_audit_logger(settings.audit_log_path)
        self._agents: dict[str, Agent] = {}
        self._gateways: dict[str, TelegramGateway] = (
            shared_gateways if shared_gateways is not None else {}
        )
        self._owner_gateway: TelegramGateway | None = None
        self._stop = threading.Event()
        self._offset: int | None = None

    def stop(self) -> None:
        self._stop.set()

    def run_in_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.run_forever, name="telegram-bot", daemon=True)
        thread.start()
        return thread

    def run_forever(self) -> None:
        print("Telegram bot polling…")
        print(f"Local device: {self.settings.device_id} ({self.settings.device_name})")
        print(f"Pair code (secret): {self.pairing.pair_code}")
        print(f"Owner chat: {self.pairing.owner_chat_id or '(none yet)'}")
        print(f"Pairing open: {self.pairing.pairing_open}")
        print(f"Devices file: {self.devices.path}")
        print()

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

    def owner_gateway(self) -> TelegramGateway | None:
        owner = self.pairing.owner_chat_id
        if not owner:
            return None
        if self._owner_gateway is None or str(self._owner_gateway.chat_id) != str(owner):
            key = str(owner)
            gateway = self._gateways.get(key)
            if gateway is None:
                gateway = TelegramGateway(
                    client=self.client,
                    chat_id=owner,
                    timeout_seconds=self.settings.confirmation_timeout_seconds,
                )
                self._gateways[key] = gateway
            self._owner_gateway = gateway
        return self._owner_gateway

    def _dispatch(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
            return
        message = update.get("message") or update.get("edited_message")
        if message:
            self._handle_message(message)

    def _notify_owner(self, text: str) -> None:
        owner = self.pairing.owner_chat_id
        if not owner:
            return
        try:
            self.client.send_message(owner, text)
        except TelegramAPIError:
            pass

    def _invalidate_agent(self, chat_id: int | str) -> None:
        self._agents.pop(str(chat_id), None)

    def _agent_for(self, chat_id: int | str) -> Agent:
        key = str(chat_id)
        agent = self._agents.get(key)
        if agent is None:
            profile = self.users.get(chat_id)
            role = self.pairing.role_of(chat_id)
            agent = Agent(
                self.registry,
                self.settings,
                confirm_fn=self._confirm_as_owner,
                audit=self.audit,
                source=f"telegram:{chat_id}",
                role=role,
                requester_id=str(chat_id),
                user_profile=profile,
                devices=self.devices,
                active_device_id=profile.active_device_id or self.devices.local_id,
            )
            self._agents[key] = agent
        return agent

    def _confirm_as_owner(self, prompt: str, _use_telegram: bool) -> bool:
        gateway = self.owner_gateway()
        if gateway is None:
            self.audit.log("confirmation_blocked", error="No owner chat")
            return False
        return gateway.ask(redact_secrets(prompt))

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

        if chat_id is None or not self.pairing.is_owner(chat_id):
            self.audit.log("confirmation_rejected_non_owner", chat_id=chat_id)
            return

        parts = data.split(":")
        if len(parts) != 3 or parts[0] != "confirm":
            return
        request_id, decision = parts[1], parts[2]
        allowed = decision == "yes"
        gateway = self.owner_gateway()
        found = bool(gateway and gateway.resolve(request_id, allowed))

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
        lowered = text.lower()

        if lowered.startswith("/pair"):
            self._handle_pair(chat_id, text)
            return
        if lowered in {"/whoami", "whoami"}:
            role = self.pairing.role_of(chat_id).value
            self.client.send_message(
                chat_id,
                f"chat_id={chat_id}\nrole={role}\n"
                f"Set TELEGRAM_OWNER_CHAT_ID={chat_id} on your PC .env if you are the owner.",
            )
            return

        # BYOK commands available even before pair? No — require pair except whoami/pair.
        if not self.pairing.is_allowed(chat_id):
            self.audit.log("unauthorized_message", chat_id=chat_id, text=text[:80])
            self._notify_owner(f"⚠️ Unauthorized chat tried to talk: {chat_id}")
            self.client.send_message(chat_id, "Unauthorized.")
            return

        message_id = message.get("message_id")

        # Config / BYOK
        if lowered.startswith("/apikey"):
            self._cmd_apikey(chat_id, text, message_id=message_id)
            return
        if lowered.startswith("/baseurl"):
            self._cmd_baseurl(chat_id, text)
            return
        if lowered.startswith("/model"):
            self._cmd_model(chat_id, text)
            return
        if lowered in {"/myconfig", "myconfig"}:
            profile = self.users.get(chat_id)
            lines = [f"{k}: {v}" for k, v in profile.public_dict().items()]
            self.client.send_message(chat_id, "Your config:\n" + "\n".join(lines))
            return

        # Devices
        if lowered in {"/devices", "devices"}:
            self._cmd_devices(chat_id)
            return
        if lowered.startswith("/use"):
            self._cmd_use(chat_id, text)
            return
        if lowered.startswith("/device_add"):
            self._cmd_device_add(chat_id, text)
            return
        if lowered.startswith("/device_del"):
            self._cmd_device_del(chat_id, text)
            return

        # Owner admin
        if lowered.startswith("/invite"):
            self._cmd_invite(chat_id, text)
            return
        if lowered in {"/friends", "friends"}:
            members = self.pairing.list_members()
            self.client.send_message(
                chat_id,
                f"Owner: {members['owner']}\nFriends: {members['friends'] or '(none)'}",
            )
            return
        if lowered in {"/lock", "lock"}:
            if not self.pairing.is_owner(chat_id):
                self.client.send_message(chat_id, "Owner only.")
                return
            self.pairing.set_pairing_open(False)
            self.client.send_message(chat_id, "Pairing locked.")
            return
        if lowered in {"/unlock", "unlock"}:
            if not self.pairing.is_owner(chat_id):
                self.client.send_message(chat_id, "Owner only.")
                return
            self.pairing.set_pairing_open(True)
            code = self.pairing.rotate_code()
            print(f"Pairing unlocked. Host code: {code}")
            self.client.send_message(
                chat_id,
                "Host pairing unlocked briefly.\nPrefer /invite for friends.\n"
                f"Host code (also on PC): `{code}`",
            )
            return
        if lowered.startswith("/revoke"):
            if not self.pairing.is_owner(chat_id):
                self.client.send_message(chat_id, "Owner only.")
                return
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                self.client.send_message(chat_id, "Usage: /revoke CHAT_ID")
                return
            target = parts[1].strip()
            ok = self.pairing.revoke(target)
            self._invalidate_agent(target)
            self.client.send_message(
                chat_id,
                f"Revoked {target}." if ok else "Could not revoke (missing/owner).",
            )
            return

        if media is not None:
            self._handle_inbound_media(chat_id, media, caption=text)
            return
        if not text:
            return

        if lowered in {"/start", "start", "/help", "help"}:
            self.client.send_message(chat_id, HELP_TEXT)
            return
        if lowered in {"/tools", "tools"}:
            agent = self._agent_for(chat_id)
            names = [
                t.get("function", {}).get("name")
                for t in agent._tools_for_role()
            ]
            self.client.send_message(
                chat_id,
                "Tools for your role:\n" + "\n".join(f"• {n}" for n in names if n),
            )
            return
        if lowered in {"/reset", "reset"}:
            self._invalidate_agent(chat_id)
            self.client.send_message(chat_id, "Conversation reset.")
            return
        if lowered in {"/status", "status"}:
            self._send_status(chat_id)
            return
        if lowered in {"/inbox", "inbox"}:
            self.client.send_message(
                chat_id,
                f"Inbox: {self.settings.inbox_dir.resolve()}\n"
                f"Max: {self.settings.safety.inbound_max_bytes} bytes",
            )
            return
        if lowered.startswith("/audit"):
            if not self.pairing.is_owner(chat_id):
                self.client.send_message(chat_id, "Owner only.")
                return
            self._send_audit(chat_id, text)
            return
        if text.startswith("/"):
            self.client.send_message(chat_id, "Unknown command. Try /help.")
            return

        if (
            self.pairing.is_friend(chat_id)
            and self.settings.permissions.notify_owner_on_friend_command
        ):
            self._notify_owner(f"👤 Friend {chat_id}: {text[:200]}")

        self._run_agent(chat_id, text)

    def _cmd_apikey(
        self,
        chat_id: int | str,
        text: str,
        *,
        message_id: int | None = None,
    ) -> None:
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            self.client.send_message(
                chat_id,
                "Usage: /apikey sk-...  (or /apikey clear)\n\n" + APIKEY_WARNING,
            )
            return
        value = parts[1].strip()
        cleared = value.lower() == "clear"
        if cleared:
            self.users.set_api_key(chat_id, None)
        else:
            self.users.set_api_key(chat_id, value)
        self._invalidate_agent(chat_id)
        # Best-effort scrub of the secret-bearing user message.
        deleted = False
        if message_id is not None and not cleared:
            try:
                self.client.delete_message(chat_id, int(message_id))
                deleted = True
            except TelegramAPIError:
                deleted = False
        if cleared:
            self.client.send_message(chat_id, "API key cleared for your profile.")
        else:
            scrub = (
                "Your /apikey message was deleted from this chat."
                if deleted
                else "Could not auto-delete your /apikey message — delete it yourself."
            )
            self.client.send_message(
                chat_id,
                "API key saved for your profile only (not shared).\n"
                f"{scrub}\n"
                "Keys remain recoverable from Telegram backups if the message stays.",
            )
        self.audit.log("user_apikey_set", chat_id=chat_id, cleared=cleared, scrubbed=deleted)

    def _cmd_baseurl(self, chat_id: int | str, text: str) -> None:
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            self.client.send_message(
                chat_id,
                "Usage: /baseurl https://api.openai.com/v1  (or /baseurl clear)",
            )
            return
        value = parts[1].strip()
        self.users.set_base_url(chat_id, None if value.lower() == "clear" else value)
        self._invalidate_agent(chat_id)
        self.client.send_message(chat_id, "Base URL updated for your profile.")

    def _cmd_model(self, chat_id: int | str, text: str) -> None:
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            self.client.send_message(chat_id, "Usage: /model gpt-4o-mini  (or /model clear)")
            return
        value = parts[1].strip()
        self.users.set_model(chat_id, None if value.lower() == "clear" else value)
        self._invalidate_agent(chat_id)
        self.client.send_message(chat_id, "Model updated for your profile.")

    def _cmd_devices(self, chat_id: int | str) -> None:
        profile = self.users.get(chat_id)
        active = profile.active_device_id or self.devices.local_id
        rows = self.devices.visible_to(chat_id, is_owner=self.pairing.is_owner(chat_id))
        lines = [f"Active: {active}", "Devices:"]
        for d in rows:
            mark = "←" if d.id == active else ""
            mode = "local" if d.is_local else "remote"
            lines.append(f"• {d.id} — {d.name} ({mode}) {mark}")
        self.client.send_message(chat_id, "\n".join(lines))

    def _cmd_use(self, chat_id: int | str, text: str) -> None:
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            self.client.send_message(chat_id, "Usage: /use DEVICE_ID")
            return
        device_id = parts[1].strip()
        visible = {
            d.id: d
            for d in self.devices.visible_to(chat_id, is_owner=self.pairing.is_owner(chat_id))
        }
        if device_id not in visible:
            self.client.send_message(chat_id, "Unknown or unauthorized device.")
            return
        self.users.set_active_device(chat_id, device_id)
        self._invalidate_agent(chat_id)
        ping = ping_device(visible[device_id])
        status = "online" if ping.get("ok") else f"unreachable ({ping.get('error')})"
        self.client.send_message(chat_id, f"Active device → {device_id} ({status})")

    def _cmd_device_add(self, chat_id: int | str, text: str) -> None:
        if not self.pairing.is_owner(chat_id):
            self.client.send_message(chat_id, "Owner only.")
            return
        parsed = _parse_device_add(text)
        if parsed is None:
            self.client.send_message(
                chat_id,
                "Usage:\n"
                "• /device_add ID NAME URL TOKEN\n"
                "• /device_add ID \"Name With Spaces\" URL TOKEN\n"
                "• /device_add ID|Name With Spaces|URL|TOKEN\n"
                "Example: /device_add laptop \"Home Laptop\" http://192.168.1.20:8765 secrettoken",
            )
            return
        device_id, name, url, token = parsed
        self.devices.upsert(
            device_id,
            name=name,
            base_url=url,
            token=token,
            owner_chat_id=str(chat_id),
        )
        self.client.send_message(chat_id, f"Device saved: {device_id} ({name})")

    def _cmd_device_del(self, chat_id: int | str, text: str) -> None:
        if not self.pairing.is_owner(chat_id):
            self.client.send_message(chat_id, "Owner only.")
            return
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            self.client.send_message(chat_id, "Usage: /device_del ID")
            return
        ok = self.devices.remove(parts[1].strip())
        self.client.send_message(chat_id, "Removed." if ok else "Cannot remove (missing/local).")

    def _cmd_invite(self, chat_id: int | str, text: str) -> None:
        if not self.pairing.is_owner(chat_id):
            self.client.send_message(chat_id, "Owner only.")
            return
        label = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
        invite = self.pairing.create_invite(chat_id, label=label)
        if invite is None:
            self.client.send_message(
                chat_id,
                f"Cannot create invite (friend limit {self.pairing.max_friends}?).",
            )
            return
        mins = int((invite.expires_at - invite.created_at) / 60)
        self.client.send_message(
            chat_id,
            "One-time friend invite (send privately):\n"
            f"`/pair {invite.code}`\n"
            f"Expires in ~{mins} minutes. Auto-locks after use.",
        )
        print(f"Invite created for friend: {invite.code} label={label!r}")

    def _handle_pair(self, chat_id: int | str, text: str) -> None:
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            self.client.send_message(chat_id, "Usage: /pair CODE")
            return
        result = self.pairing.try_pair(chat_id, parts[1].strip())
        self.audit.log("pair_attempt", chat_id=chat_id, result=result.value)
        if result == PairResult.OK:
            role = self.pairing.role_of(chat_id).value
            print(f"Paired {chat_id} as {role}")
            self.client.send_message(
                chat_id,
                f"Paired ✅ as {role}.\nNext: /apikey YOUR_KEY then send a command.\n/devices to pick a device.",
            )
            if self.pairing.is_owner(chat_id):
                self.client.send_message(chat_id, HELP_TEXT)
            self._notify_owner(f"ℹ️ Chat paired: {chat_id} ({role})")
            return
        if result == PairResult.ALREADY_PAIRED:
            self.client.send_message(chat_id, "Already paired.")
            return
        if result == PairResult.RATE_LIMITED:
            self.client.send_message(chat_id, "Too many attempts. Try later.")
            self._notify_owner(f"⚠️ Pair rate-limit: {chat_id}")
            return
        if result == PairResult.INVITE_REQUIRED:
            self.client.send_message(chat_id, "Pairing rejected.")
            self._notify_owner(f"⚠️ Pair without invite from {chat_id}")
            return
        if result == PairResult.FRIEND_LIMIT:
            self.client.send_message(chat_id, "Friend limit reached.")
            return
        self.client.send_message(chat_id, "Pairing rejected.")
        self._notify_owner(f"⚠️ Failed /pair from {chat_id} ({result.value})")

    def _send_status(self, chat_id: int | str) -> None:
        profile = self.users.get(chat_id)
        device = self.devices.get(profile.active_device_id or self.devices.local_id)
        self.client.send_message(
            chat_id,
            "\n".join(
                [
                    "PC hub online ✅",
                    f"Role: {self.pairing.role_of(chat_id).value}",
                    f"Your API key: {'yes' if profile.has_api_key() else 'NO — /apikey'}",
                    f"Model: {profile.model or self.settings.openai_model}",
                    f"Device: {device.id if device else '?'} ({'local' if device and device.is_local else 'remote'})",
                    f"Tools: {len(self.registry)}",
                    f"Friends: {len(self.pairing.friends)}",
                ]
            ),
        )

    def _send_audit(self, chat_id: int | str, text: str) -> None:
        parts = text.split()
        limit = 10
        if len(parts) >= 2:
            try:
                limit = max(1, min(int(parts[1]), 50))
            except ValueError:
                limit = 10
        rows = self.audit.tail(limit)
        if not rows:
            self.client.send_message(chat_id, "Audit log is empty.")
            return
        lines = [f"Last {len(rows)} audit events:"]
        for row in rows:
            lines.append(
                f"• {row.get('ts', '?')} | {row.get('event')} | "
                f"{row.get('tool') or row.get('source') or ''} "
                f"{redact_secrets(str(row.get('error') or row.get('text') or ''))[:80]}"
            )
        self.client.send_message(chat_id, "\n".join(lines))

    def _handle_inbound_media(
        self,
        chat_id: int | str,
        media: dict[str, Any],
        *,
        caption: str,
    ) -> None:
        self.client.send_message(chat_id, f"📥 Saving {media.get('kind')}…")
        saved = save_telegram_media(
            self.client,
            media,
            inbox_dir=self.settings.inbox_dir,
            chat_id=chat_id,
            policy=self.settings.safety,
        )
        if not saved.get("ok"):
            self.audit.log(
                "inbound_rejected",
                source=f"telegram:{chat_id}",
                error=saved.get("error"),
            )
            self.client.send_message(chat_id, f"Failed: {saved.get('error')}")
            return
        self.audit.log(
            "inbound_saved",
            source=f"telegram:{chat_id}",
            kind=saved.get("kind"),
            path=saved.get("path"),
            size_bytes=saved.get("size_bytes"),
        )
        lines = [
            f"Saved ✅ ({saved.get('kind')})",
            f"Path: {saved.get('path')}",
            f"Size: {saved.get('size_bytes')} bytes",
        ]
        transcript = None
        if saved.get("kind") in {"voice", "audio"}:
            profile = self.users.get(chat_id)
            api_key = profile.api_key or self.settings.openai_api_key
            tr = transcribe_audio_file(saved["path"], api_key=api_key)
            if tr.get("ok"):
                transcript = tr.get("text") or ""
                lines.append(f"Transcript: {transcript}")
            else:
                lines.append(str(tr.get("error")))
        self.client.send_message(chat_id, "\n".join(lines))
        follow_up = caption.strip() or (transcript or "")
        if follow_up:
            prompt = (
                f"{follow_up}\n\n[Attached file on device at: {saved.get('path')} "
                f"(kind={saved.get('kind')})]"
            )
            self._run_agent(chat_id, prompt)

    def _run_agent(self, chat_id: int | str, text: str) -> None:
        agent = self._agent_for(chat_id)
        gateway = self.owner_gateway()
        profile = self.users.get(chat_id)
        device_id = profile.active_device_id or self.devices.local_id
        self.client.send_message(chat_id, "⏳ Working…")
        confirm_ctx = use_confirmation_gateway(gateway) if gateway else nullcontext()
        with request_context(user_id=str(chat_id), device_id=device_id), confirm_ctx:
            try:
                reply = agent.run(text)
            except Exception as exc:  # noqa: BLE001
                reply = f"Error: {redact_secrets(str(exc))}"
        self.client.send_message(chat_id, reply or "(empty reply)")


def _parse_device_add(text: str) -> tuple[str, str, str, str] | None:
    """Parse ``/device_add`` args: shlex tokens or ``id|name|url|token``."""
    raw = text.strip()
    # Drop the command token (/device_add or /device_add@BotName).
    first, _, rest = raw.partition(" ")
    if not rest.strip():
        # Entire payload may be pipe-form without spaces after command.
        if "|" in first:
            # e.g. uncommon: /device_add|id|name|url|token — ignore
            return None
        return None
    rest = rest.strip()
    if rest.count("|") >= 3:
        parts = [p.strip() for p in rest.split("|", 3)]
        if len(parts) == 4 and all(parts):
            return parts[0], parts[1], parts[2], parts[3]
        return None
    try:
        tokens = shlex.split(rest)
    except ValueError:
        return None
    if len(tokens) < 4:
        return None
    # id, name (may be multi-token if user forgot quotes — join middle), url, token
    if len(tokens) == 4:
        return tokens[0], tokens[1], tokens[2], tokens[3]
    # Extra tokens: treat last two as url/token, middle as name.
    device_id = tokens[0]
    token = tokens[-1]
    url = tokens[-2]
    name = " ".join(tokens[1:-2]).strip()
    if not (device_id and name and url and token):
        return None
    return device_id, name, url, token
