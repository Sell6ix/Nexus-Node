#!/usr/bin/env python3
"""Smoke: API keys encrypted at rest."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.accounts import AccountStore
from core.secrets import PREFIX, SecretBox
from core.users import UserStore


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        box = SecretBox(tmp_path / "secrets.key")
        sealed = box.seal("sk-test-secret-key")
        assert sealed and sealed.startswith(PREFIX)
        assert "sk-test" not in sealed
        assert box.open(sealed) == "sk-test-secret-key"
        assert box.open("legacy-plain") == "legacy-plain"
        print("OK secret box")

        store = AccountStore(tmp_path / "accounts.json", secrets=box)
        acc = store.register("alice", "secret12", display_name="Alice")
        acc.api_key = "sk-live-not-for-disk"
        store.save(acc)
        raw = json.loads((tmp_path / "accounts.json").read_text(encoding="utf-8"))
        stored = raw["accounts"][0]["api_key"]
        assert stored.startswith(PREFIX), stored
        assert "sk-live" not in json.dumps(raw)
        again = AccountStore(tmp_path / "accounts.json", secrets=box)
        loaded = again.get(acc.id)
        assert loaded is not None and loaded.api_key == "sk-live-not-for-disk"
        print("OK account encrypt")

        users = UserStore(tmp_path / "users", secrets=box)
        users.set_api_key("42", "sk-tg-legacy-key")
        disk = json.loads((tmp_path / "users" / "42.json").read_text(encoding="utf-8"))
        assert str(disk["api_key"]).startswith(PREFIX)
        assert users.get("42").api_key == "sk-tg-legacy-key"
        print("OK user store encrypt")

    print("All secrets smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
