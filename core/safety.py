"""Safety gates for shell commands and inbound media."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# Patterns that are blocked even after user confirmation.
DEFAULT_SHELL_DENY_PATTERNS: tuple[str, ...] = (
    r"rm\s+-rf\s+/(?:\s|$)",
    r"rm\s+-rf\s+/\*",
    r"mkfs(\.|$|\s)",
    r"\bdd\b.*\bof=/dev/",
    r"\bdd\b.*\bif=/dev/",
    r":\(\)\s*\{\s*:\|:&\s*\}\s*;",  # classic fork bomb
    r"chmod\s+-R\s+777\s+/",
    r"chown\s+-R\s+.*( /| /)",
    r">\s*/dev/sd[a-z]",
    r"(curl|wget).*\|\s*(ba)?sh",
    r"\|\s*(ba)?sh\s*$",
    r"shutdown\b",
    r"reboot\b",
    r"poweroff\b",
    r"init\s+0",
    r"mkfs\.",
    r"diskpart\b",
    r"format\s+[a-zA-Z]:",
    r"Remove-Item\s+-Recurse\s+-Force\s+[A-Za-z]:\\",
)


@dataclass(frozen=True)
class SafetyPolicy:
    """Runtime safety knobs loaded from settings/env."""

    shell_enabled: bool = True
    shell_deny_patterns: tuple[str, ...] = DEFAULT_SHELL_DENY_PATTERNS
    shell_allow_prefixes: tuple[str, ...] = ()
    inbound_max_bytes: int = 25 * 1024 * 1024  # 25 MiB
    inbound_allowed_kinds: tuple[str, ...] = (
        "document",
        "photo",
        "voice",
        "audio",
        "video",
        "video_note",
    )


def check_shell_command(command: str, policy: SafetyPolicy) -> dict[str, Any]:
    """Return ``{ok: True}`` or ``{ok: False, error: ...}`` for a shell command."""
    cmd = (command or "").strip()
    if not cmd:
        return {"ok": False, "error": "command must not be empty."}
    if not policy.shell_enabled:
        return {
            "ok": False,
            "error": "Shell execution is disabled (SHELL_ENABLED=false).",
        }

    if policy.shell_allow_prefixes:
        lowered = cmd.lower()
        allowed = any(
            lowered.startswith(prefix.lower()) for prefix in policy.shell_allow_prefixes if prefix
        )
        if not allowed:
            return {
                "ok": False,
                "error": (
                    "Command blocked: not in SHELL_ALLOW_PREFIXES. "
                    f"Allowed prefixes: {list(policy.shell_allow_prefixes)}"
                ),
                "blocked_by": "allowlist",
            }

    for pattern in policy.shell_deny_patterns:
        try:
            if re.search(pattern, cmd, flags=re.IGNORECASE):
                return {
                    "ok": False,
                    "error": f"Command blocked by deny pattern: {pattern}",
                    "blocked_by": "deny_pattern",
                    "pattern": pattern,
                }
        except re.error:
            continue

    return {"ok": True}


def check_inbound_media(media: dict[str, Any], policy: SafetyPolicy) -> dict[str, Any]:
    """Validate inbound media kind/size before download."""
    kind = str(media.get("kind") or "")
    if kind and kind not in policy.inbound_allowed_kinds:
        return {
            "ok": False,
            "error": f"Inbound kind {kind!r} is not allowed.",
            "blocked_by": "kind",
        }

    size = media.get("file_size")
    if size is not None:
        try:
            size_i = int(size)
        except (TypeError, ValueError):
            size_i = None
        if size_i is not None and size_i > policy.inbound_max_bytes:
            return {
                "ok": False,
                "error": (
                    f"File too large: {size_i} bytes "
                    f"(limit {policy.inbound_max_bytes})."
                ),
                "blocked_by": "size",
                "file_size": size_i,
                "limit": policy.inbound_max_bytes,
            }

    return {"ok": True}


def parse_csv_patterns(raw: str | None, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Split a comma/newline-separated env string into patterns."""
    if raw is None or not raw.strip():
        return default
    parts = []
    for chunk in re.split(r"[\n,]", raw):
        item = chunk.strip()
        if item:
            parts.append(item)
    return tuple(parts) if parts else default
