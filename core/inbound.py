"""Save inbound Telegram media to the host PC and optionally transcribe voice."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from core.telegram_api import TelegramAPIError, TelegramClient


def default_inbox_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "inbox"


def extract_media(message: dict[str, Any]) -> dict[str, Any] | None:
    """Return a normalized media descriptor from a Telegram message, or None."""
    if document := message.get("document"):
        return {
            "kind": "document",
            "file_id": document.get("file_id"),
            "file_unique_id": document.get("file_unique_id"),
            "file_name": document.get("file_name") or "document.bin",
            "mime_type": document.get("mime_type"),
            "file_size": document.get("file_size"),
        }
    if photos := message.get("photo"):
        # Largest size is last.
        photo = photos[-1]
        return {
            "kind": "photo",
            "file_id": photo.get("file_id"),
            "file_unique_id": photo.get("file_unique_id"),
            "file_name": f"photo_{photo.get('file_unique_id', 'img')}.jpg",
            "mime_type": "image/jpeg",
            "file_size": photo.get("file_size"),
        }
    if voice := message.get("voice"):
        return {
            "kind": "voice",
            "file_id": voice.get("file_id"),
            "file_unique_id": voice.get("file_unique_id"),
            "file_name": f"voice_{voice.get('file_unique_id', 'msg')}.ogg",
            "mime_type": voice.get("mime_type") or "audio/ogg",
            "file_size": voice.get("file_size"),
        }
    if audio := message.get("audio"):
        name = audio.get("file_name") or f"audio_{audio.get('file_unique_id', 'track')}.mp3"
        return {
            "kind": "audio",
            "file_id": audio.get("file_id"),
            "file_unique_id": audio.get("file_unique_id"),
            "file_name": name,
            "mime_type": audio.get("mime_type"),
            "file_size": audio.get("file_size"),
        }
    if video := message.get("video"):
        name = video.get("file_name") or f"video_{video.get('file_unique_id', 'clip')}.mp4"
        return {
            "kind": "video",
            "file_id": video.get("file_id"),
            "file_unique_id": video.get("file_unique_id"),
            "file_name": name,
            "mime_type": video.get("mime_type"),
            "file_size": video.get("file_size"),
        }
    if video_note := message.get("video_note"):
        return {
            "kind": "video_note",
            "file_id": video_note.get("file_id"),
            "file_unique_id": video_note.get("file_unique_id"),
            "file_name": f"videonote_{video_note.get('file_unique_id', 'note')}.mp4",
            "mime_type": "video/mp4",
            "file_size": video_note.get("file_size"),
        }
    return None


def save_telegram_media(
    client: TelegramClient,
    media: dict[str, Any],
    *,
    inbox_dir: Path | None = None,
    chat_id: int | str | None = None,
) -> dict[str, Any]:
    """Download media described by ``extract_media`` into the inbox directory."""
    file_id = media.get("file_id")
    if not file_id:
        return {"ok": False, "error": "Missing file_id"}

    root = Path(inbox_dir) if inbox_dir else default_inbox_dir()
    day = dt.datetime.now().strftime("%Y-%m-%d")
    chat_part = str(chat_id) if chat_id is not None else "unknown"
    dest_dir = root / chat_part / day
    dest_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(str(media.get("file_name") or "file.bin")).name
    stamp = dt.datetime.now().strftime("%H%M%S")
    dest = dest_dir / f"{stamp}_{safe_name}"

    try:
        meta = client.get_file(str(file_id))
        remote_path = meta.get("file_path")
        if not remote_path:
            return {"ok": False, "error": f"getFile returned no file_path: {meta}"}
        client.download_file(str(remote_path), dest)
    except TelegramAPIError as exc:
        return {"ok": False, "error": str(exc)}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "kind": media.get("kind"),
        "path": str(dest.resolve()),
        "size_bytes": dest.stat().st_size,
        "mime_type": media.get("mime_type"),
        "original_name": safe_name,
    }


def transcribe_audio_file(path: str | Path, *, api_key: str | None) -> dict[str, Any]:
    """Transcribe an audio/voice file with OpenAI Whisper when a key is available."""
    if not api_key:
        return {
            "ok": False,
            "skipped": True,
            "error": "OPENAI_API_KEY missing — voice saved without transcription.",
        }
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        with Path(path).open("rb") as handle:
            result = client.audio.transcriptions.create(
                model="whisper-1",
                file=handle,
            )
        text = getattr(result, "text", None) or str(result)
        return {"ok": True, "text": text.strip()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
