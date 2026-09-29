"""Append-only JSONL audit log for dangerous / notable actions."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLogger:
    """Thread-safe JSONL writer under ``data/audit.jsonl`` (configurable)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(self, event: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        line = json.dumps(record, default=str, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def tail(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the last ``limit`` audit records (best-effort)."""
        if not self.path.is_file():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in lines[-max(1, min(limit, 200)) :]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


_default: AuditLogger | None = None
_default_lock = threading.Lock()


def get_audit_logger(path: Path | None = None) -> AuditLogger:
    """Return a process-wide audit logger (lazily created)."""
    global _default
    with _default_lock:
        if path is not None:
            _default = AuditLogger(path)
            return _default
        if _default is None:
            root = Path(__file__).resolve().parent.parent / "data" / "audit.jsonl"
            _default = AuditLogger(root)
        return _default
