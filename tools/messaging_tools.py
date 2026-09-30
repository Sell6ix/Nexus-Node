"""Contacts and outbound messaging (email / Telegram) with confirmation."""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from core.confirmation import require_confirmation
from core.contacts import ContactStore
from core.context import get_user_id
from core.registry import tool


def _store() -> ContactStore:
    root = Path(__file__).resolve().parent.parent / "data" / "contacts"
    return ContactStore(root)


def _smtp_configured() -> bool:
    return bool(
        (os.getenv("SMTP_HOST") or "").strip()
        and (os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or "").strip()
    )


@tool
def list_contacts() -> dict[str, Any]:
    """List saved contacts for the current user (name, email, telegram).

    Returns:
        Contacts the assistant may message after confirmation.
    """
    try:
        rows = _store().list(get_user_id())
        public = [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "email": c.get("email"),
                "telegram": c.get("telegram"),
                "notes": c.get("notes"),
            }
            for c in rows
        ]
        return {"ok": True, "count": len(public), "contacts": public}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def add_contact(
    name: str,
    email: str = "",
    telegram: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Add or update a contact in the user's address book.

    Args:
        name: Display name (unique per user, case-insensitive).
        email: Email address (optional if telegram set).
        telegram: Telegram chat id or @username (optional if email set).
        notes: Optional free-text note.

    Returns:
        The saved contact.
    """
    try:
        contact = _store().upsert(
            get_user_id(),
            name=name,
            email=email,
            telegram=telegram,
            notes=notes,
        )
        return {"ok": True, "contact": contact}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def remove_contact(name_or_id: str) -> dict[str, Any]:
    """Remove a contact by id or exact name.

    Args:
        name_or_id: Contact id or name.

    Returns:
        Whether a contact was removed.
    """
    try:
        ok = _store().remove(get_user_id(), name_or_id.strip())
        return {"ok": ok, "removed": ok}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _resolve_contact(who: str) -> dict[str, Any] | None:
    store = _store()
    uid = get_user_id()
    found = store.find(uid, who)
    if found:
        return found
    # allow raw email / telegram id without a saved contact
    w = who.strip()
    if "@" in w and " " not in w and not w.startswith("@"):
        return {"name": w, "email": w, "telegram": None, "id": None}
    if w.lstrip("@").isdigit() or w.startswith("@"):
        return {"name": w, "email": None, "telegram": w.lstrip("@"), "id": None}
    return None


@tool
@require_confirmation(
    "Send an email from this hub to the chosen contact.",
    telegram_button_hook=True,
)
def send_email(to: str, subject: str, body: str) -> dict[str, Any]:
    """Send an email via the hub SMTP settings. Requires confirmation.

    ``to`` may be a saved contact name or a raw email address.
    Configure ``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USER``, ``SMTP_PASSWORD``,
    ``SMTP_FROM`` (and optional ``SMTP_TLS`` / ``SMTP_STARTTLS``) on the hub.

    Args:
        to: Contact name or email address.
        subject: Email subject.
        body: Plain-text body.

    Returns:
        Delivery status.
    """
    try:
        if not _smtp_configured():
            return {
                "ok": False,
                "error": "SMTP not configured. Set SMTP_HOST and SMTP_FROM (and usually SMTP_USER/PASSWORD) in .env",
            }
        contact = _resolve_contact(to)
        if contact is None:
            return {
                "ok": False,
                "error": f"Unknown contact '{to}'. Use add_contact or list_contacts first.",
            }
        email_addr = (contact.get("email") or "").strip()
        if not email_addr:
            return {"ok": False, "error": f"Contact '{contact.get('name')}' has no email."}
        subj = (subject or "").strip() or "(no subject)"
        text = (body or "").strip()
        if not text:
            return {"ok": False, "error": "body must not be empty"}

        host = os.environ["SMTP_HOST"].strip()
        port = int(os.getenv("SMTP_PORT") or "587")
        user = (os.getenv("SMTP_USER") or "").strip() or None
        password = (os.getenv("SMTP_PASSWORD") or "").strip() or None
        mail_from = (os.getenv("SMTP_FROM") or user or "").strip()
        use_ssl = (os.getenv("SMTP_TLS") or "").strip().lower() in {"1", "true", "yes", "on"}
        starttls = (os.getenv("SMTP_STARTTLS") or "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        msg = EmailMessage()
        msg["Subject"] = subj
        msg["From"] = mail_from
        msg["To"] = email_addr
        msg.set_content(text)

        if use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=context, timeout=45) as smtp:
                if user and password:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=45) as smtp:
                smtp.ehlo()
                if starttls:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                if user and password:
                    smtp.login(user, password)
                smtp.send_message(msg)
        return {
            "ok": True,
            "to": email_addr,
            "contact": contact.get("name"),
            "subject": subj,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
@require_confirmation(
    "Send a Telegram message to the chosen contact via the hub bot.",
    telegram_button_hook=True,
)
def send_telegram_message(to: str, text: str) -> dict[str, Any]:
    """Send a Telegram message via the hub bot. Requires confirmation.

    ``to`` may be a saved contact name or a numeric chat id / @username.
    The recipient must have started a chat with the bot first.
    Needs ``TELEGRAM_BOT_TOKEN`` on the hub.

    Args:
        to: Contact name, chat id, or @username.
        text: Message body.

    Returns:
        Telegram API result or error.
    """
    try:
        token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        if not token:
            return {
                "ok": False,
                "error": "TELEGRAM_BOT_TOKEN not set on the hub.",
            }
        contact = _resolve_contact(to)
        if contact is None:
            return {
                "ok": False,
                "error": f"Unknown contact '{to}'. Use add_contact or list_contacts first.",
            }
        chat = (contact.get("telegram") or "").strip()
        if not chat:
            return {
                "ok": False,
                "error": f"Contact '{contact.get('name')}' has no telegram id.",
            }
        body = (text or "").strip()
        if not body:
            return {"ok": False, "error": "text must not be empty"}
        from core.telegram_api import TelegramClient

        client = TelegramClient(token)
        result = client.send_message(chat, body)
        return {
            "ok": True,
            "to": chat,
            "contact": contact.get("name"),
            "telegram_message_id": (result or {}).get("message_id"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
