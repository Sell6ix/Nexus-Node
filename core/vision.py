"""Describe / OCR images with the user's OpenAI-compatible vision model."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def describe_image(
    data: bytes,
    *,
    content_type: str,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
) -> str | None:
    """Return a short description + any readable text from an image, or None."""
    key = (api_key or "").strip()
    if not key or not data:
        return None
    ctype = (content_type or "image/jpeg").split(";")[0].strip() or "image/jpeg"
    if len(data) > 8 * 1024 * 1024:
        return None
    b64 = base64.b64encode(data).decode("ascii")
    url = f"{base_url.rstrip('/')}/chat/completions"
    body: dict[str, Any] = {
        "model": model or "gpt-4o-mini",
        "max_tokens": 500,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Describe this image briefly for a personal assistant. "
                            "If there is readable text (signs, screenshots, documents), "
                            "transcribe the important parts. Reply in the same language "
                            "as the text if present, otherwise English. Keep under 400 words."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{ctype};base64,{b64}"},
                    },
                ],
            }
        ],
    }
    req = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "NexusNode/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    choices = payload.get("choices") or []
    if not choices:
        return None
    msg = (choices[0].get("message") or {}).get("content") or ""
    text = str(msg).strip()
    return text[:4000] if text else None
