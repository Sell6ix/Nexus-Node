"""Web Push (VAPID) for reminders when the PWA is closed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import base64


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class PushStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.private_pem: str = ""
        self.public_b64: str = ""
        self.subs: dict[str, list[dict[str, Any]]] = {}
        self._load()

    def _load(self) -> None:
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
        else:
            raw = {}
        self.private_pem = str(raw.get("private_pem") or "")
        self.public_b64 = str(raw.get("public_b64") or "")
        self.subs = {
            str(k): list(v)
            for k, v in dict(raw.get("subscriptions") or {}).items()
            if isinstance(v, list)
        }
        if not self.private_pem or not self.public_b64:
            self._generate_keys()
            self._save()

    def _generate_keys(self) -> None:
        key = ec.generate_private_key(ec.SECP256R1())
        self.private_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        public = key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
        self.public_b64 = _b64url(public)

    def _save(self) -> None:
        payload = {
            "private_pem": self.private_pem,
            "public_b64": self.public_b64,
            "subscriptions": self.subs,
        }
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def add(self, user_id: str, subscription: dict[str, Any]) -> None:
        endpoint = str(subscription.get("endpoint") or "")
        keys = subscription.get("keys") or {}
        if not endpoint or not isinstance(keys, dict):
            raise ValueError("Invalid push subscription")
        row = {
            "endpoint": endpoint,
            "keys": {
                "p256dh": str(keys.get("p256dh") or ""),
                "auth": str(keys.get("auth") or ""),
            },
        }
        bucket = [s for s in self.subs.get(user_id, []) if s.get("endpoint") != endpoint]
        bucket.append(row)
        self.subs[user_id] = bucket[-8:]
        self._save()

    def remove(self, user_id: str, endpoint: str) -> None:
        self.subs[user_id] = [
            s for s in self.subs.get(user_id, []) if s.get("endpoint") != endpoint
        ]
        self._save()

    def send(self, user_id: str, title: str, body: str) -> int:
        """Send a push to all of this user's devices. Returns successes."""
        try:
            from pywebpush import WebPushException, webpush
        except ImportError:
            print("[push] pywebpush not installed — pip install pywebpush")
            return 0
        sent = 0
        dead: list[str] = []
        for sub in list(self.subs.get(user_id, [])):
            try:
                webpush(
                    subscription_info=sub,
                    data=json.dumps({"title": title, "body": body, "url": "/"}),
                    vapid_private_key=self.private_pem,
                    vapid_claims={"sub": "mailto:nexus-node@localhost"},
                )
                sent += 1
            except WebPushException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in {404, 410}:
                    dead.append(str(sub.get("endpoint") or ""))
                else:
                    print(f"[push] failed: {exc}")
            except Exception as exc:  # noqa: BLE001
                print(f"[push] failed: {exc}")
        if dead:
            for ep in dead:
                self.remove(user_id, ep)
        return sent
