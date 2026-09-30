"""Google OAuth2 (Gmail + Calendar) token storage and API helpers."""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from core.secrets import SecretBox

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly "
    "https://www.googleapis.com/auth/gmail.send "
    "https://www.googleapis.com/auth/calendar.events "
    "openid email profile"
).split()


class GoogleOAuthError(RuntimeError):
    pass


def google_configured() -> bool:
    return bool(
        (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
        and (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
    )


def redirect_uri() -> str:
    return (
        os.getenv("GOOGLE_REDIRECT_URI")
        or os.getenv("NEXUS_PUBLIC_URL", "http://127.0.0.1:8743").rstrip("/")
        + "/api/google/callback"
    )


class GoogleTokenStore:
    """Per-user Google tokens under ``data/google/<user>.json`` (refresh sealed)."""

    def __init__(self, root: Path, secrets: SecretBox) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.secrets = secrets

    def _path(self, user_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(user_id))
        return self.root / f"{safe or 'user'}.json"

    def get(self, user_id: str) -> dict[str, Any] | None:
        path = self._path(user_id)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        refresh = self.secrets.open(raw.get("refresh_token_enc"))
        access = self.secrets.open(raw.get("access_token_enc"))
        if not refresh and not access:
            return None
        return {
            "refresh_token": refresh,
            "access_token": access,
            "expires_at": float(raw.get("expires_at") or 0),
            "email": raw.get("email"),
            "scope": raw.get("scope"),
        }

    def save(
        self,
        user_id: str,
        *,
        access_token: str,
        refresh_token: str | None,
        expires_in: int,
        email: str | None = None,
        scope: str | None = None,
    ) -> None:
        existing = self.get(user_id) or {}
        refresh = refresh_token or existing.get("refresh_token")
        payload = {
            "access_token_enc": self.secrets.seal(access_token),
            "refresh_token_enc": self.secrets.seal(refresh) if refresh else None,
            "expires_at": time.time() + max(60, int(expires_in or 3600)) - 30,
            "email": email or existing.get("email"),
            "scope": scope or existing.get("scope"),
            "updated_at": time.time(),
        }
        self._path(user_id).write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )

    def clear(self, user_id: str) -> bool:
        path = self._path(user_id)
        if path.is_file():
            path.unlink()
            return True
        return False

    def status(self, user_id: str) -> dict[str, Any]:
        tok = self.get(user_id)
        return {
            "configured": google_configured(),
            "connected": bool(tok and (tok.get("refresh_token") or tok.get("access_token"))),
            "email": (tok or {}).get("email"),
            "redirect_uri": redirect_uri(),
        }


def auth_url(*, state: str) -> str:
    if not google_configured():
        raise GoogleOAuthError("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env")
    q = urllib.parse.urlencode(
        {
            "client_id": os.environ["GOOGLE_CLIENT_ID"].strip(),
            "redirect_uri": redirect_uri(),
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    return f"{AUTH_URL}?{q}"


def exchange_code(code: str) -> dict[str, Any]:
    return _token_request(
        {
            "code": code,
            "client_id": os.environ["GOOGLE_CLIENT_ID"].strip(),
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"].strip(),
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        }
    )


def refresh_access(refresh_token: str) -> dict[str, Any]:
    return _token_request(
        {
            "refresh_token": refresh_token,
            "client_id": os.environ["GOOGLE_CLIENT_ID"].strip(),
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"].strip(),
            "grant_type": "refresh_token",
        }
    )


def _token_request(form: dict[str, str]) -> dict[str, Any]:
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise GoogleOAuthError(f"Token exchange failed: {exc}") from exc


def _http_json(
    url: str,
    *,
    access_token: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> Any:
    raw = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=raw,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if raw else {}),
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return json.loads(text) if text.strip() else {}
    except Exception as exc:  # noqa: BLE001
        raise GoogleOAuthError(str(exc)) from exc


class GoogleClient:
    """Access-token helper with auto-refresh."""

    def __init__(self, store: GoogleTokenStore, user_id: str) -> None:
        self.store = store
        self.user_id = user_id

    def access_token(self) -> str:
        tok = self.store.get(self.user_id)
        if not tok:
            raise GoogleOAuthError("Google not connected. Settings → Connect Google.")
        if tok.get("access_token") and float(tok.get("expires_at") or 0) > time.time():
            return str(tok["access_token"])
        refresh = tok.get("refresh_token")
        if not refresh:
            raise GoogleOAuthError("Google session expired. Reconnect in Settings.")
        fresh = refresh_access(str(refresh))
        access = str(fresh.get("access_token") or "")
        if not access:
            raise GoogleOAuthError("Could not refresh Google token.")
        self.store.save(
            self.user_id,
            access_token=access,
            refresh_token=str(fresh.get("refresh_token") or refresh),
            expires_in=int(fresh.get("expires_in") or 3600),
            email=tok.get("email"),
            scope=fresh.get("scope") or tok.get("scope"),
        )
        return access

    def fetch_email(self) -> str | None:
        data = _http_json(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            access_token=self.access_token(),
        )
        return str(data.get("email") or "") or None

    def list_messages(self, *, max_results: int = 10, query: str = "is:unread") -> list[dict[str, Any]]:
        q = urllib.parse.urlencode(
            {"maxResults": str(max(1, min(max_results, 20))), "q": query}
        )
        listing = _http_json(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages?{q}",
            access_token=self.access_token(),
        )
        out: list[dict[str, Any]] = []
        for item in listing.get("messages") or []:
            mid = item.get("id")
            if not mid:
                continue
            meta = _http_json(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}"
                f"?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date",
                access_token=self.access_token(),
            )
            headers = {
                h.get("name"): h.get("value")
                for h in (meta.get("payload") or {}).get("headers") or []
            }
            out.append(
                {
                    "id": mid,
                    "from": headers.get("From"),
                    "subject": headers.get("Subject"),
                    "date": headers.get("Date"),
                    "snippet": meta.get("snippet"),
                }
            )
        return out

    def send_email(self, *, to: str, subject: str, body: str) -> dict[str, Any]:
        import base64
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        # From is implied by the connected account
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        return _http_json(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            access_token=self.access_token(),
            method="POST",
            body={"raw": raw},
        )

    def list_events(self, *, days: int = 7) -> list[dict[str, Any]]:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        end = now + timedelta(days=max(1, min(days, 60)))
        q = urllib.parse.urlencode(
            {
                "timeMin": now.isoformat().replace("+00:00", "Z"),
                "timeMax": end.isoformat().replace("+00:00", "Z"),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": "30",
            }
        )
        data = _http_json(
            f"https://www.googleapis.com/calendar/v3/calendars/primary/events?{q}",
            access_token=self.access_token(),
        )
        out: list[dict[str, Any]] = []
        for ev in data.get("items") or []:
            start = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date")
            end_at = (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date")
            out.append(
                {
                    "id": ev.get("id"),
                    "title": ev.get("summary"),
                    "start": start,
                    "end": end_at,
                    "location": ev.get("location"),
                    "htmlLink": ev.get("htmlLink"),
                }
            )
        return out

    def create_event(
        self,
        *,
        title: str,
        start_iso: str,
        end_iso: str,
        location: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "summary": title,
            "start": {"dateTime": start_iso, "timeZone": "UTC"},
            "end": {"dateTime": end_iso, "timeZone": "UTC"},
        }
        if location:
            body["location"] = location
        if description:
            body["description"] = description
        return _http_json(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            access_token=self.access_token(),
            method="POST",
            body=body,
        )
