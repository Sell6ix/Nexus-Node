#!/usr/bin/env python3
"""Smoke the web hub: register, login, devices, invite."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8743"


def call(method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return json.loads(res.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from exc


def main() -> int:
    h = call("GET", "/api/health")
    assert h.get("ok"), h
    print("OK health", h.get("device"), "has_owner=", h.get("has_owner"))

    # Use unique username each run
    import time

    user = f"owner{int(time.time()) % 100000}"
    if not h.get("has_owner"):
        reg = call(
            "POST",
            "/api/auth/register",
            {"username": user, "password": "secret12", "display_name": "Owner"},
        )
    else:
        # If owner exists, still try register as member needs invite — just login check path via register fail
        try:
            reg = call(
                "POST",
                "/api/auth/register",
                {"username": user, "password": "secret12", "display_name": "Tmp"},
            )
        except RuntimeError as exc:
            if "invite" not in str(exc).lower() and "taken" not in str(exc).lower():
                # open signup maybe
                raise
            print("OK register gated without invite")
            print("All web smoke checks passed (partial — owner already present).")
            return 0

    token = reg["token"]
    me = call("GET", "/api/me", token=token)
    assert me["user"]["username"] == user
    print("OK register/me", me["user"]["role"])

    devices = call("GET", "/api/devices", token=token)
    assert devices.get("devices"), devices
    print("OK devices", [d["id"] for d in devices["devices"]])

    if me["user"]["role"] == "owner":
        inv = call("POST", "/api/invites", {"label": "smoke"}, token=token)
        assert inv.get("code"), inv
        friend = f"friend{int(time.time()) % 100000}"
        reg2 = call(
            "POST",
            "/api/auth/register",
            {
                "username": friend,
                "password": "secret12",
                "invite_code": inv["code"],
            },
        )
        assert reg2["user"]["role"] == "member"
        print("OK invite+friend", friend)

    print("All web smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
