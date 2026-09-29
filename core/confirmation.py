"""Confirmation gate for dangerous tool operations (console + Telegram)."""

from __future__ import annotations

import functools
import threading
import uuid
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

ATTR_REQUIRES_CONFIRMATION = "_requires_confirmation"
ATTR_CONFIRMATION_MESSAGE = "_confirmation_message"
ATTR_TELEGRAM_BUTTON_HOOK = "_telegram_button_hook"


class ConfirmationDenied(Exception):
    """Raised when the user declines a confirmation prompt."""


class ConfirmationGateway(Protocol):
    """Channel that can ask the user to approve a dangerous action."""

    def ask(self, prompt: str) -> bool: ...


@dataclass
class ConsoleGateway:
    """Ask for Y/N on the local console (PC CLI)."""

    def ask(self, prompt: str) -> bool:
        print("\n⚠️  Confirmation required")
        print(f"   {prompt}")
        while True:
            answer = input("   Proceed? [y/N]: ").strip().lower()
            if answer in {"y", "yes"}:
                return True
            if answer in {"", "n", "no"}:
                return False
            print("   Please answer 'y' or 'n'.")


@dataclass
class PendingConfirmation:
    event: threading.Event = field(default_factory=threading.Event)
    allowed: bool | None = None


@dataclass
class TelegramGateway:
    """Ask via Telegram inline Yes/No buttons (phone or desktop Telegram)."""

    client: Any  # TelegramClient — typed loosely to avoid circular imports
    chat_id: int | str
    pending: dict[str, PendingConfirmation] = field(default_factory=dict)
    timeout_seconds: float = 120.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def ask(self, prompt: str) -> bool:
        from core.telegram_api import inline_confirm_keyboard

        request_id = uuid.uuid4().hex[:12]
        pending = PendingConfirmation()
        with self.lock:
            self.pending[request_id] = pending

        text = f"⚠️ Confirmation required\n\n{prompt}\n\nTap a button within {int(self.timeout_seconds)}s."
        try:
            self.client.send_message(
                self.chat_id,
                text,
                reply_markup=inline_confirm_keyboard(request_id),
            )
        except Exception:
            with self.lock:
                self.pending.pop(request_id, None)
            raise

        finished = pending.event.wait(timeout=self.timeout_seconds)
        with self.lock:
            self.pending.pop(request_id, None)

        if not finished or pending.allowed is None:
            return False
        return pending.allowed

    def resolve(self, request_id: str, allowed: bool) -> bool:
        """Resolve a pending confirmation from a callback_query. Returns True if found."""
        with self.lock:
            pending = self.pending.get(request_id)
            if pending is None:
                return False
            pending.allowed = allowed
            pending.event.set()
            return True


@dataclass
class PreferTelegramThenConsoleGateway:
    """Try Telegram buttons first; fall back to console if Telegram is unavailable."""

    telegram: TelegramGateway | None
    console: ConsoleGateway = field(default_factory=ConsoleGateway)

    def ask(self, prompt: str) -> bool:
        if self.telegram is not None:
            try:
                return self.telegram.ask(prompt)
            except Exception as exc:  # noqa: BLE001
                print(f"(Telegram confirmation failed: {exc}; falling back to console)")
        return self.console.ask(prompt)


_current_gateway: ContextVar[ConfirmationGateway | None] = ContextVar(
    "nexus_confirmation_gateway",
    default=None,
)
_default_gateway: ConfirmationGateway = ConsoleGateway()


def set_confirmation_gateway(gateway: ConfirmationGateway | None) -> None:
    """Set the process-default gateway (used when no context override is active)."""
    global _default_gateway
    _default_gateway = gateway or ConsoleGateway()


def use_confirmation_gateway(gateway: ConfirmationGateway) -> Any:
    """Context-manager helper: temporarily bind a gateway for the current context."""

    class _Token:
        def __enter__(self) -> ConfirmationGateway:
            self._token = _current_gateway.set(gateway)
            return gateway

        def __exit__(self, *args: object) -> None:
            _current_gateway.reset(self._token)

    return _Token()


def get_confirmation_gateway() -> ConfirmationGateway:
    return _current_gateway.get() or _default_gateway


def require_confirmation(
    message: str | None = None,
    *,
    telegram_button_hook: bool = True,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Mark a tool as requiring explicit user confirmation before execution.

    Confirmation goes through the active :class:`ConfirmationGateway`:
    console Y/N on PC CLI, or Telegram inline buttons when the Telegram bot
    (or a PreferTelegramThenConsole gateway) is active.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        setattr(func, ATTR_REQUIRES_CONFIRMATION, True)
        setattr(func, ATTR_CONFIRMATION_MESSAGE, message)
        setattr(func, ATTR_TELEGRAM_BUTTON_HOOK, telegram_button_hook)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            prompt = message or _default_prompt(func.__name__, kwargs or {})
            if not confirm_action(prompt, use_telegram_stub=telegram_button_hook):
                raise ConfirmationDenied(
                    f"User denied confirmation for tool '{func.__name__}'."
                )
            return func(*args, **kwargs)

        setattr(wrapper, ATTR_REQUIRES_CONFIRMATION, True)
        setattr(wrapper, ATTR_CONFIRMATION_MESSAGE, message)
        setattr(wrapper, ATTR_TELEGRAM_BUTTON_HOOK, telegram_button_hook)
        return wrapper

    return decorator


def needs_confirmation(func: Callable[..., Any]) -> bool:
    return bool(getattr(func, ATTR_REQUIRES_CONFIRMATION, False))


def confirmation_message_for(
    func: Callable[..., Any],
    arguments: dict[str, Any],
) -> str:
    custom = getattr(func, ATTR_CONFIRMATION_MESSAGE, None)
    if custom:
        return custom
    return _default_prompt(func.__name__, arguments)


def uses_telegram_button_hook(func: Callable[..., Any]) -> bool:
    return bool(getattr(func, ATTR_TELEGRAM_BUTTON_HOOK, False))


def confirm_action(prompt: str, *, use_telegram_stub: bool = True) -> bool:
    """Ask the active confirmation gateway.

    ``use_telegram_stub`` is kept for backward compatibility with call sites;
    the active gateway already decides console vs Telegram.
    """
    _ = use_telegram_stub
    return get_confirmation_gateway().ask(prompt)


def ask_telegram_confirmation(prompt: str) -> bool | None:
    """Compatibility helper: ask only if the active gateway is Telegram-capable.

    Returns True/False when handled, or None when the caller should fall back.
    """
    gateway = get_confirmation_gateway()
    if isinstance(gateway, TelegramGateway):
        return gateway.ask(prompt)
    if isinstance(gateway, PreferTelegramThenConsoleGateway) and gateway.telegram:
        return gateway.telegram.ask(prompt)
    _ = prompt
    return None


def _default_prompt(tool_name: str, arguments: dict[str, Any]) -> str:
    pretty_args = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
    if pretty_args:
        return f"Allow tool '{tool_name}' with arguments: {pretty_args}?"
    return f"Allow tool '{tool_name}'?"
