"""Per-user chat file uploads."""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

MAX_BYTES = 15 * 1024 * 1024
ALLOWED_PREFIXES = (
    "image/",
    "text/",
    "application/pdf",
    "application/json",
    "application/msword",
    "application/vnd.",
)


class UploadStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _user_dir(self, user_id: str) -> Path:
        safe = re.sub(r"[^\w\-]", "_", str(user_id)) or "user"
        path = self.root / safe
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _meta_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "index.json"

    def list(self, user_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        path = self._meta_path(user_id)
        if not path.is_file():
            return []
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(rows, list):
            return []
        return list(reversed(rows))[: max(1, min(limit, 100))]

    def _save_index(self, user_id: str, rows: list[dict[str, Any]]) -> None:
        self._meta_path(user_id).write_text(
            json.dumps(rows[-200:], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def save(
        self,
        user_id: str,
        *,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        if len(data) > MAX_BYTES:
            raise ValueError(f"File too large (max {MAX_BYTES // (1024 * 1024)}MB)")
        ctype = (content_type or "application/octet-stream").split(";")[0].strip().lower()
        if not any(ctype.startswith(p) or ctype == p.rstrip("/") for p in ALLOWED_PREFIXES):
            if ctype not in {"application/octet-stream"}:
                raise ValueError(f"Unsupported type: {ctype}")
        orig = Path(filename or "file").name
        safe_name = re.sub(r"[^\w.\-]+", "_", orig)[:80] or "file"
        file_id = uuid.uuid4().hex[:12]
        stored = f"{file_id}_{safe_name}"
        dest = self._user_dir(user_id) / stored
        dest.write_bytes(data)
        meta = {
            "id": file_id,
            "filename": orig,
            "stored_name": stored,
            "content_type": ctype,
            "bytes": len(data),
            "created_at": time.time(),
            "path": str(dest),
        }
        # text preview for small text files
        if ctype.startswith("text/") or ctype in {"application/json"}:
            try:
                meta["text_preview"] = data.decode("utf-8", errors="replace")[:4000]
            except Exception:  # noqa: BLE001
                pass
        rows = []
        idx = self._meta_path(user_id)
        if idx.is_file():
            try:
                rows = list(json.loads(idx.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                rows = []
        rows.append(meta)
        self._save_index(user_id, rows)
        return meta

    def get(self, user_id: str, file_id: str) -> dict[str, Any] | None:
        for row in self.list(user_id, limit=200):
            if row.get("id") == file_id:
                return row
        return None

    def set_text_preview(self, user_id: str, file_id: str, preview: str) -> dict[str, Any] | None:
        path = self._meta_path(user_id)
        if not path.is_file():
            return None
        try:
            rows = list(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return None
        updated: dict[str, Any] | None = None
        for row in rows:
            if isinstance(row, dict) and row.get("id") == file_id:
                row["text_preview"] = str(preview)[:4000]
                updated = row
                break
        if updated is None:
            return None
        self._save_index(user_id, rows)
        return updated

    def read_bytes(self, user_id: str, file_id: str) -> tuple[dict[str, Any], bytes] | None:
        meta = self.get(user_id, file_id)
        if not meta:
            return None
        path = Path(str(meta.get("path") or ""))
        if not path.is_file():
            path = self._user_dir(user_id) / str(meta.get("stored_name") or "")
        if not path.is_file():
            return None
        return meta, path.read_bytes()
