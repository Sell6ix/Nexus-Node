"""Everyday productivity tools: notes, todos, reminders."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.registry import tool

_DATA = Path(__file__).resolve().parent.parent / "data" / "daily"


def _ensure() -> None:
    _DATA.mkdir(parents=True, exist_ok=True)


def _notes_path() -> Path:
    _ensure()
    return _DATA / "notes.json"


def _todos_path() -> Path:
    _ensure()
    return _DATA / "todos.json"


def _reminders_path() -> Path:
    _ensure()
    return _DATA / "reminders.json"


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return list(raw) if isinstance(raw, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@tool
def add_note(text: str, tags: str = "") -> dict[str, Any]:
    """Save a short personal note on this device.

    Args:
        text: Note body.
        tags: Optional comma-separated tags.

    Returns:
        The created note id and preview.
    """
    try:
        body = text.strip()
        if not body:
            return {"ok": False, "error": "text must not be empty"}
        rows = _load(_notes_path())
        note = {
            "id": uuid.uuid4().hex[:10],
            "text": body,
            "tags": [t.strip() for t in tags.split(",") if t.strip()],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        rows.append(note)
        _save(_notes_path(), rows[-500:])
        return {"ok": True, "note": note}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def list_notes(limit: int = 20, query: str = "") -> dict[str, Any]:
    """List recent notes, optionally filtered by substring.

    Args:
        limit: Max notes to return (default 20).
        query: Optional case-insensitive search string.

    Returns:
        Matching notes newest-first.
    """
    try:
        rows = list(reversed(_load(_notes_path())))
        q = query.strip().lower()
        if q:
            rows = [n for n in rows if q in str(n.get("text", "")).lower()]
        return {"ok": True, "count": len(rows[: max(1, min(limit, 100))]), "notes": rows[: max(1, min(limit, 100))]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def add_todo(title: str, due: str = "") -> dict[str, Any]:
    """Add a todo item on this device.

    Args:
        title: What to do.
        due: Optional due hint (free text or ISO date).

    Returns:
        The created todo.
    """
    try:
        body = title.strip()
        if not body:
            return {"ok": False, "error": "title must not be empty"}
        rows = _load(_todos_path())
        item = {
            "id": uuid.uuid4().hex[:10],
            "title": body,
            "due": due.strip() or None,
            "done": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        rows.append(item)
        _save(_todos_path(), rows[-500:])
        return {"ok": True, "todo": item}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def list_todos(include_done: bool = False) -> dict[str, Any]:
    """List todos on this device.

    Args:
        include_done: Include completed items (default false).

    Returns:
        Open (and optionally done) todos.
    """
    try:
        rows = _load(_todos_path())
        if not include_done:
            rows = [t for t in rows if not t.get("done")]
        return {"ok": True, "count": len(rows), "todos": rows[-100:]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def complete_todo(todo_id: str) -> dict[str, Any]:
    """Mark a todo as done by id.

    Args:
        todo_id: Todo id from list_todos / add_todo.

    Returns:
        Updated todo or error.
    """
    try:
        rows = _load(_todos_path())
        for item in rows:
            if item.get("id") == todo_id:
                item["done"] = True
                item["completed_at"] = datetime.now(timezone.utc).isoformat()
                _save(_todos_path(), rows)
                return {"ok": True, "todo": item}
        return {"ok": False, "error": f"Todo not found: {todo_id}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def set_reminder(text: str, when: str) -> dict[str, Any]:
    """Store a reminder note on this device (local list; not a push scheduler).

    Args:
        text: Reminder text.
        when: When to remember (free text or ISO datetime).

    Returns:
        The stored reminder. The assistant should also mention it in replies;
        OS push scheduling is not guaranteed.
    """
    try:
        body = text.strip()
        if not body:
            return {"ok": False, "error": "text must not be empty"}
        rows = _load(_reminders_path())
        item = {
            "id": uuid.uuid4().hex[:10],
            "text": body,
            "when": when.strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "done": False,
        }
        rows.append(item)
        _save(_reminders_path(), rows[-500:])
        return {
            "ok": True,
            "reminder": item,
            "note": "Stored locally. For phone push, ask the hub to send a Telegram message at that time (manual/cron).",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def list_reminders(include_done: bool = False) -> dict[str, Any]:
    """List stored reminders on this device.

    Args:
        include_done: Include completed reminders.

    Returns:
        Reminder rows.
    """
    try:
        rows = _load(_reminders_path())
        if not include_done:
            rows = [r for r in rows if not r.get("done")]
        return {"ok": True, "count": len(rows), "reminders": rows[-100:]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
