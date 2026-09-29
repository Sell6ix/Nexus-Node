"""Confirmation gate for dangerous tool operations."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

# Attribute names stamped onto decorated callables.
ATTR_REQUIRES_CONFIRMATION = "_requires_confirmation"
ATTR_CONFIRMATION_MESSAGE = "_confirmation_message"
ATTR_TELEGRAM_BUTTON_HOOK = "_telegram_button_hook"


class ConfirmationDenied(Exception):
    """Raised when the user declines a confirmation prompt."""


def require_confirmation(
    message: str | None = None,
    *,
    telegram_button_hook: bool = True,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Mark a tool as requiring explicit user confirmation before execution.

    When the agent invokes the tool, the confirmation handler asks for a
    console Y/N answer. ``telegram_button_hook`` reserves an optional
    Telegram inline-button confirmation path (currently a stub).

    Args:
        message: Optional override shown to the user. Defaults to a
            message derived from the tool name and arguments.
        telegram_button_hook: When True, the confirmation flow may also
            call :func:`ask_telegram_confirmation` (stub).
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        setattr(func, ATTR_REQUIRES_CONFIRMATION, True)
        setattr(func, ATTR_CONFIRMATION_MESSAGE, message)
        setattr(func, ATTR_TELEGRAM_BUTTON_HOOK, telegram_button_hook)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # Direct calls still go through confirmation so CLI/tests stay safe.
            prompt = message or _default_prompt(func.__name__, kwargs or {})
            if not confirm_action(prompt, use_telegram_stub=telegram_button_hook):
                raise ConfirmationDenied(
                    f"User denied confirmation for tool '{func.__name__}'."
                )
            return func(*args, **kwargs)

        # Preserve markers on the wrapper so the registry can discover them.
        setattr(wrapper, ATTR_REQUIRES_CONFIRMATION, True)
        setattr(wrapper, ATTR_CONFIRMATION_MESSAGE, message)
        setattr(wrapper, ATTR_TELEGRAM_BUTTON_HOOK, telegram_button_hook)
        return wrapper

    return decorator


def needs_confirmation(func: Callable[..., Any]) -> bool:
    """Return True if ``func`` was decorated with ``@require_confirmation``."""
    return bool(getattr(func, ATTR_REQUIRES_CONFIRMATION, False))


def confirmation_message_for(
    func: Callable[..., Any],
    arguments: dict[str, Any],
) -> str:
    """Build the confirmation prompt for a decorated tool call."""
    custom = getattr(func, ATTR_CONFIRMATION_MESSAGE, None)
    if custom:
        return custom
    return _default_prompt(func.__name__, arguments)


def uses_telegram_button_hook(func: Callable[..., Any]) -> bool:
    """Return True if Telegram button confirmation is opted in."""
    return bool(getattr(func, ATTR_TELEGRAM_BUTTON_HOOK, False))


def confirm_action(prompt: str, *, use_telegram_stub: bool = False) -> bool:
    """Ask the user to confirm a dangerous action.

    Primary path is console Y/N. When ``use_telegram_stub`` is True, the
    optional Telegram button hook is invoked first (currently a no-op stub
    that always returns None so the console path remains authoritative).
    """
    if use_telegram_stub:
        telegram_answer = ask_telegram_confirmation(prompt)
        if telegram_answer is not None:
            return telegram_answer

    print("\n⚠️  Confirmation required")
    print(f"   {prompt}")
    while True:
        answer = input("   Proceed? [y/N]: ").strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"", "n", "no"}:
            return False
        print("   Please answer 'y' or 'n'.")


def ask_telegram_confirmation(prompt: str) -> bool | None:
    """Optional Telegram inline-button confirmation hook (stub).

    Returns:
        True/False if a Telegram UI answered, or None to fall back to console.
    """
    # Stub: wire Telegram Bot API inline keyboards here when a chat session
    # is available. Returning None keeps the console path as the default.
    _ = prompt
    return None


def _default_prompt(tool_name: str, arguments: dict[str, Any]) -> str:
    pretty_args = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
    if pretty_args:
        return f"Allow tool '{tool_name}' with arguments: {pretty_args}?"
    return f"Allow tool '{tool_name}'?"
