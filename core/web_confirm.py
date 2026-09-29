"""In-app confirmation gateway over WebSocket (replaces Telegram buttons)."""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PendingConfirm:
    prompt: str
    event: threading.Event = field(default_factory=threading.Event)
    allowed: bool | None = None
    account_id: str = ""


class WebConfirmBroker:
    """Per-user pending confirmations + fan-out to connected sockets."""

    def __init__(self, *, timeout_seconds: float = 120.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._pending: dict[str, PendingConfirm] = {}
        self._lock = threading.Lock()
        # account_id -> set of asyncio.Queue for WS push
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._sub_lock = threading.Lock()

    def subscribe(self, account_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        with self._sub_lock:
            self._subscribers.setdefault(account_id, set()).add(queue)
        return queue

    def unsubscribe(self, account_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._sub_lock:
            buckets = self._subscribers.get(account_id)
            if not buckets:
                return
            buckets.discard(queue)
            if not buckets:
                self._subscribers.pop(account_id, None)

    def _notify(self, account_id: str, payload: dict[str, Any]) -> None:
        with self._sub_lock:
            queues = list(self._subscribers.get(account_id, set()))
        for q in queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def ask(self, account_id: str, prompt: str) -> bool:
        """Block until the user answers in the web UI (or timeout → deny)."""
        request_id = uuid.uuid4().hex[:12]
        pending = PendingConfirm(prompt=prompt, account_id=account_id)
        with self._lock:
            self._pending[request_id] = pending
        self._notify(
            account_id,
            {
                "type": "confirm",
                "id": request_id,
                "prompt": prompt,
                "timeout": int(self.timeout_seconds),
            },
        )
        finished = pending.event.wait(timeout=self.timeout_seconds)
        with self._lock:
            self._pending.pop(request_id, None)
        if not finished or pending.allowed is None:
            return False
        return bool(pending.allowed)

    def resolve(self, request_id: str, account_id: str, allowed: bool) -> bool:
        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None:
                return False
            if pending.account_id != account_id:
                return False
            pending.allowed = allowed
            pending.event.set()
            return True
