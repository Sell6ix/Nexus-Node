"""Request-scoped context (which user / device is acting)."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

_user_id: ContextVar[str | None] = ContextVar("nexus_user_id", default=None)
_device_id: ContextVar[str | None] = ContextVar("nexus_device_id", default=None)


def get_user_id() -> str:
    return _user_id.get() or "local"


def get_device_id() -> str:
    return _device_id.get() or "local"


@contextmanager
def request_context(
    *,
    user_id: str | None = None,
    device_id: str | None = None,
) -> Iterator[None]:
    tokens = []
    if user_id is not None:
        tokens.append((_user_id, _user_id.set(str(user_id))))
    if device_id is not None:
        tokens.append((_device_id, _device_id.set(str(device_id))))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
