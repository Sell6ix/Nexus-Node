"""Long-term personal memory tools (hub-local)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.context import get_user_id
from core.memory import MemoryStore
from core.registry import tool


def _store() -> MemoryStore:
    root = Path(__file__).resolve().parent.parent / "data" / "memory"
    return MemoryStore(root)


@tool
def remember(text: str) -> dict[str, Any]:
    """Save a lasting fact about the user for future chats.

    Use for stable preferences and personal context (timezone, how to address
    them, family names, routines). Do not store secrets/passwords.

    Args:
        text: Short fact to remember.

    Returns:
        The stored fact id.
    """
    try:
        fact = _store().add(get_user_id(), text)
        return {"ok": True, "fact": fact}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def list_memory() -> dict[str, Any]:
    """List all lasting memory facts for the current user.

    Returns:
        Stored facts newest-last.
    """
    try:
        facts = _store().list(get_user_id())
        return {"ok": True, "count": len(facts), "facts": facts}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def recall_memory(query: str = "") -> dict[str, Any]:
    """Search lasting memory facts (empty query = list all).

    Args:
        query: Optional substring filter.

    Returns:
        Matching facts.
    """
    try:
        facts = _store().search(get_user_id(), query)
        return {"ok": True, "count": len(facts), "facts": facts, "query": query}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool
def forget_memory(id_or_query: str) -> dict[str, Any]:
    """Delete a memory fact by id, or by unique text substring.

    Args:
        id_or_query: Fact id or unique search text.

    Returns:
        Whether something was removed.
    """
    try:
        removed = _store().remove(get_user_id(), id_or_query)
        if removed is None:
            return {
                "ok": False,
                "error": "No unique matching fact. Use list_memory and pass an id.",
            }
        return {"ok": True, "removed": removed}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
