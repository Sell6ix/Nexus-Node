"""FastAPI web hub — multi-user app UI API (phone + PC, no Telegram required)."""

from __future__ import annotations

import asyncio
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.accounts import Account, AccountStore
from core.agent import Agent
from core.audit import get_audit_logger
from core.backup import build_backup_zip
from core.briefing import BriefingNotifier, BriefingScheduleStore
from core.chat_history import ChatHistory
from core.config import Settings
from core.contacts import ContactStore
from core.context import request_context
from core.devices import DeviceRegistry, ping_device
from core.google_oauth import (
    GoogleOAuthError,
    GoogleTokenStore,
    auth_url,
    exchange_code,
    google_configured,
)
from core.netinfo import hub_urls, print_hub_banner
from core.permissions import Role
from core.push import PushStore
from core.registry import ToolRegistry
from core.reminders import ReminderNotifier, flush_app_open_reminders
from core.secrets import default_box
from core.uploads import UploadStore
from core.users import UserProfile
from core.web_confirm import WebConfirmBroker


class RegisterBody(BaseModel):
    username: str
    password: str
    display_name: str = ""
    invite_code: str | None = None


class LoginBody(BaseModel):
    username: str
    password: str


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    attachment_ids: list[str] = Field(default_factory=list)
    conversation_id: str = "main"


class ConversationBody(BaseModel):
    title: str = "New chat"


class ConversationPatch(BaseModel):
    title: str


class BriefingScheduleBody(BaseModel):
    enabled: bool = True
    hour: int = 8
    minute: int = 0
    timezone: str = "Asia/Jerusalem"
    location: str = "Tel Aviv"
    evening_enabled: bool = False
    evening_hour: int = 20
    evening_minute: int = 0


class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    voice: str = "alloy"
    language: str = ""


class ProfilePatch(BaseModel):
    api_key: str | None = None
    clear_api_key: bool = False
    api_base_url: str | None = None
    model: str | None = None
    display_name: str | None = None
    active_device_id: str | None = None
    home_device_id: str | None = None
    clear_active_device: bool = False
    clear_home_device: bool = False


class DeviceBody(BaseModel):
    id: str
    name: str
    base_url: str
    token: str


class InviteBody(BaseModel):
    label: str = ""


class ConfirmBody(BaseModel):
    id: str
    allowed: bool


class PushSubBody(BaseModel):
    endpoint: str
    keys: dict[str, str]


class ContactBody(BaseModel):
    name: str
    email: str = ""
    telegram: str = ""
    whatsapp: str = ""
    notes: str = ""
    id: str | None = None


def _account_to_profile(acc: Account) -> UserProfile:
    return UserProfile(
        chat_id=acc.id,
        api_key=acc.api_key,
        api_base_url=acc.api_base_url,
        model=acc.model,
        active_device_id=acc.active_device_id,
        home_device_id=acc.home_device_id,
        display_name=acc.display_name,
    )


def create_app(
    settings: Settings,
    registry: ToolRegistry,
    *,
    open_signup: bool | None = None,
    tls: bool = False,
    bind_host: str | None = None,
    bind_port: int | None = None,
) -> FastAPI:
    open_signup = (
        open_signup
        if open_signup is not None
        else os.getenv("NEXUS_OPEN_SIGNUP", "").strip().lower() in {"1", "true", "yes", "on"}
    )
    jwt_secret = os.getenv("NEXUS_JWT_SECRET") or _load_or_create_secret(
        settings.data_dir / "jwt_secret.txt"
    )
    secret_box = default_box(settings.data_dir)
    accounts = AccountStore(
        settings.data_dir / "accounts.json",
        max_members=int(os.getenv("NEXUS_MAX_MEMBERS", "50")),
        secrets=secret_box,
    )
    devices = DeviceRegistry(
        settings.devices_path,
        local_id=settings.device_id,
        local_name=settings.device_name,
        local_token=settings.worker_token,
    )
    # Tag local device as hub-owned until users claim devices.
    audit = get_audit_logger(settings.audit_log_path)
    broker = WebConfirmBroker(timeout_seconds=settings.confirmation_timeout_seconds)
    push = PushStore(settings.data_dir / "push.json")
    chats = ChatHistory(settings.data_dir / "chats")
    contacts = ContactStore(settings.data_dir / "contacts")
    google_tokens = GoogleTokenStore(settings.data_dir / "google", secret_box)
    uploads = UploadStore(settings.data_dir / "uploads")
    briefing_schedules = BriefingScheduleStore(settings.data_dir / "briefing")
    agents: dict[str, Agent] = {}
    agents_guard = threading.Lock()
    agent_locks: dict[str, threading.Lock] = {}
    agent_locks_guard = threading.Lock()

    app = FastAPI(title="Nexus Node", version="0.3.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _agent_key(user_id: str, conversation_id: str = "main") -> str:
        cid = (conversation_id or "main").strip() or "main"
        return f"{user_id}::{cid}"

    def _lock_for(user_id: str, conversation_id: str = "main") -> threading.Lock:
        key = _agent_key(user_id, conversation_id)
        with agent_locks_guard:
            lock = agent_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                agent_locks[key] = lock
            return lock

    def _agent_for(account: Account, conversation_id: str = "main") -> Agent:
        key = _agent_key(account.id, conversation_id)
        with agents_guard:
            agent = agents.get(key)
            role = Role.OWNER if account.role == "owner" else Role.FRIEND
            device_id = account.resolve_device_id(devices.local_id)
            profile = _account_to_profile(account)

            def confirm_fn(prompt: str, _use_tg: bool) -> bool:
                return broker.ask(account.id, prompt)

            if agent is None:
                agent = Agent(
                    registry,
                    settings,
                    confirm_fn=confirm_fn,
                    audit=audit,
                    source=f"web:{account.id}:{conversation_id}",
                    role=role,
                    requester_id=account.id,
                    user_profile=profile,
                    devices=devices,
                    active_device_id=device_id,
                )
                transcript = chats.load(account.id, conversation_id)
                agent.seed_from_transcript(ChatHistory.llm_turns(transcript))
                agents[key] = agent
            else:
                agent.confirm_fn = confirm_fn
                agent.user_profile = profile
                agent.active_device_id = device_id
                agent.role = role
                agent.client = agent._build_client()
            return agent

    def _token_for(account: Account) -> str:
        payload = {
            "sub": account.id,
            "username": account.username,
            "role": account.role,
            "exp": datetime.now(timezone.utc) + timedelta(days=14),
        }
        return jwt.encode(payload, jwt_secret, algorithm="HS256")

    def _account_from_auth(authorization: str | None) -> Account:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "Not authenticated")
        token = authorization.split(" ", 1)[1].strip()
        try:
            data = jwt.decode(token, jwt_secret, algorithms=["HS256"])
        except jwt.PyJWTError as exc:
            raise HTTPException(401, "Invalid token") from exc
        acc = accounts.get(str(data.get("sub") or ""))
        if acc is None or not acc.enabled:
            raise HTTPException(401, "Account missing")
        return acc

    def current_account(authorization: str | None = None) -> Account:
        # FastAPI dependency injected via Header
        raise NotImplementedError

    from fastapi import Header

    def require_account(authorization: str | None = Header(default=None)) -> Account:
        return _account_from_auth(authorization)

    def require_owner(account: Account = Depends(require_account)) -> Account:
        if account.role != "owner":
            raise HTTPException(403, "Owner only")
        return account

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        port = bind_port or int(os.getenv("NEXUS_WEB_PORT", "8743"))
        host = bind_host or os.getenv("NEXUS_WEB_HOST", "0.0.0.0")
        scheme = "https" if tls else "http"
        return {
            "ok": True,
            "product": "Nexus Node",
            "device": settings.device_id,
            "has_owner": accounts.has_owner,
            "open_signup": open_signup or not accounts.has_owner,
            "tools": len(registry),
            "urls": hub_urls(host, port, scheme=scheme),
            "push": True,
            "tls": tls,
        }

    @app.post("/api/auth/register")
    def register(body: RegisterBody) -> dict[str, Any]:
        try:
            acc = accounts.register(
                body.username,
                body.password,
                display_name=body.display_name,
                invite_code=body.invite_code,
                open_signup=open_signup,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        acc.home_device_id = acc.home_device_id or settings.device_id
        accounts.save(acc)
        audit.log("web_register", user=acc.username, role=acc.role)
        return {"token": _token_for(acc), "user": acc.public_dict()}

    @app.post("/api/auth/login")
    def login(body: LoginBody) -> dict[str, Any]:
        acc = accounts.authenticate(body.username, body.password)
        if acc is None:
            raise HTTPException(401, "Invalid username or password")
        audit.log("web_login", user=acc.username)
        return {"token": _token_for(acc), "user": acc.public_dict()}

    def _flush_open_reminders(account: Account) -> None:
        try:
            flush_app_open_reminders(
                settings.data_dir / "daily",
                account.id,
                send=_send_reminder,
            )
        except Exception:  # noqa: BLE001
            pass

    @app.get("/api/me")
    def me(account: Account = Depends(require_account)) -> dict[str, Any]:
        _flush_open_reminders(account)
        return {"user": account.public_dict()}

    @app.patch("/api/me")
    def patch_me(
        body: ProfilePatch, account: Account = Depends(require_account)
    ) -> dict[str, Any]:
        if body.clear_api_key:
            account.api_key = None
        elif body.api_key is not None:
            account.api_key = body.api_key.strip() or None
        if body.api_base_url is not None:
            account.api_base_url = body.api_base_url.strip().rstrip("/") or None
        if body.model is not None:
            account.model = body.model.strip() or None
        if body.display_name is not None:
            account.display_name = body.display_name.strip() or account.username
        if body.clear_active_device:
            account.active_device_id = None
        elif body.active_device_id is not None:
            account.active_device_id = body.active_device_id.strip() or None
        if body.clear_home_device:
            account.home_device_id = None
        elif body.home_device_id is not None:
            account.home_device_id = body.home_device_id.strip() or None
        accounts.save(account)
        return {"user": account.public_dict()}

    @app.get("/api/devices")
    def list_devices(account: Account = Depends(require_account)) -> dict[str, Any]:
        is_owner = account.role == "owner"
        rows = devices.visible_to(account.id, is_owner=is_owner)
        # Also show local hub to everyone on this install
        by_id = {d.id: d for d in rows}
        local = devices.get(devices.local_id)
        if local and local.id not in by_id:
            by_id[local.id] = local
        active = account.resolve_device_id(devices.local_id)
        return {
            "active": active,
            "home": account.home_device_id,
            "devices": [
                {
                    "id": d.id,
                    "name": d.name,
                    "mode": "local" if d.is_local else "remote",
                    "enabled": d.enabled,
                }
                for d in sorted(by_id.values(), key=lambda x: x.id)
            ],
        }

    @app.post("/api/devices")
    def add_device(
        body: DeviceBody, account: Account = Depends(require_account)
    ) -> dict[str, Any]:
        devices.upsert(
            body.id.strip(),
            name=body.name.strip() or body.id,
            base_url=body.base_url.strip(),
            token=body.token.strip(),
            owner_chat_id=account.id,
        )
        return {"ok": True, "id": body.id.strip()}

    @app.delete("/api/devices/{device_id}")
    def del_device(
        device_id: str, account: Account = Depends(require_account)
    ) -> dict[str, Any]:
        dev = devices.get(device_id)
        if dev is None:
            raise HTTPException(404, "Not found")
        if account.role != "owner" and str(dev.owner_chat_id) != account.id:
            raise HTTPException(403, "Not your device")
        if not devices.remove(device_id):
            raise HTTPException(400, "Cannot remove local hub device")
        return {"ok": True}

    @app.post("/api/devices/{device_id}/ping")
    def ping(device_id: str, account: Account = Depends(require_account)) -> dict[str, Any]:
        _ = account
        dev = devices.get(device_id)
        if dev is None:
            raise HTTPException(404, "Not found")
        return ping_device(dev)

    @app.post("/api/invites")
    def create_invite(
        body: InviteBody, account: Account = Depends(require_owner)
    ) -> dict[str, Any]:
        inv = accounts.create_invite(account.id, label=body.label)
        return {
            "code": inv.code,
            "label": inv.label,
            "expires_at": inv.expires_at,
        }

    @app.get("/api/users")
    def list_users(account: Account = Depends(require_owner)) -> dict[str, Any]:
        _ = account
        return {"users": accounts.list_public()}

    @app.get("/api/tools")
    def list_tools(account: Account = Depends(require_account)) -> dict[str, Any]:
        role = Role.OWNER if account.role == "owner" else Role.FRIEND
        agent = Agent(
            registry,
            settings,
            role=role,
            requester_id=account.id,
            user_profile=_account_to_profile(account),
            devices=devices,
            active_device_id=account.resolve_device_id(devices.local_id),
        )
        names = [
            t.get("function", {}).get("name")
            for t in agent._tools_for_role()
        ]
        return {"tools": [n for n in names if n]}

    @app.get("/api/assistant")
    def assistant_status(account: Account = Depends(require_account)) -> dict[str, Any]:
        brave = bool(
            (os.getenv("BRAVE_API_KEY") or os.getenv("BRAVE_SEARCH_API_KEY") or "").strip()
        )
        tavily = bool((os.getenv("TAVILY_API_KEY") or "").strip())
        smtp = bool(
            (os.getenv("SMTP_HOST") or "").strip()
            and (os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or "").strip()
        )
        return {
            "web_search": {
                "ready": True,
                "providers": {
                    "brave": brave,
                    "tavily": tavily,
                    "duckduckgo_fallback": True,
                    "wikipedia_fallback": True,
                },
                "preferred": "brave" if brave else ("tavily" if tavily else "duckduckgo"),
            },
            "fetch_url": {"ready": True},
            "email": {
                "ready": smtp,
                "from": (os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or None)
                if smtp
                else None,
            },
            "telegram_messaging": {
                "ready": bool((os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()),
            },
            "whatsapp_messaging": {
                "ready": bool(
                    (os.getenv("WHATSAPP_TOKEN") or "").strip()
                    and (os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
                ),
            },
            "contacts": {"ready": True},
            "google": google_tokens.status(account.id),
            "briefing": {
                "ready": True,
                "schedule": briefing_schedules.get(account.id),
                "weather": True,
            },
            "hq_voice": {"ready": bool((account.api_key or settings.openai_api_key or "").strip())},
            "uploads": {"ready": True, "image_describe": True},
            "backup": {"ready": account.role == "owner"},
            "setup": {
                "has_api_key": bool((account.api_key or settings.openai_api_key or "").strip()),
                "briefing_enabled": bool(briefing_schedules.get(account.id).get("enabled")),
                "google_connected": bool(google_tokens.status(account.id).get("connected")),
                "push_configured": bool(push.subs.get(account.id)),
            },
        }

    @app.get("/api/contacts")
    def list_contacts_api(account: Account = Depends(require_account)) -> dict[str, Any]:
        return {"contacts": contacts.list(account.id)}

    @app.post("/api/contacts")
    def upsert_contact_api(
        body: ContactBody, account: Account = Depends(require_account)
    ) -> dict[str, Any]:
        try:
            contact = contacts.upsert(
                account.id,
                name=body.name,
                email=body.email,
                telegram=body.telegram,
                whatsapp=body.whatsapp,
                notes=body.notes,
                contact_id=body.id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"contact": contact}

    @app.delete("/api/contacts/{contact_id}")
    def delete_contact_api(
        contact_id: str, account: Account = Depends(require_account)
    ) -> dict[str, Any]:
        ok = contacts.remove(account.id, contact_id)
        if not ok:
            raise HTTPException(404, "Contact not found")
        return {"ok": True}

    @app.get("/api/google/status")
    def google_status_api(account: Account = Depends(require_account)) -> dict[str, Any]:
        return google_tokens.status(account.id)

    @app.get("/api/google/connect")
    def google_connect(account: Account = Depends(require_account)) -> dict[str, Any]:
        if not google_configured():
            raise HTTPException(
                400,
                "Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REDIRECT_URI (or NEXUS_PUBLIC_URL) on the hub.",
            )
        state = jwt.encode(
            {
                "sub": account.id,
                "purpose": "google_oauth",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=20),
            },
            jwt_secret,
            algorithm="HS256",
        )
        try:
            url = auth_url(state=state)
        except GoogleOAuthError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"url": url}

    @app.get("/api/google/callback")
    def google_callback(code: str = "", state: str = "", error: str = "") -> RedirectResponse:
        if error:
            return RedirectResponse(url=f"/?google=error&detail={error}", status_code=302)
        try:
            data = jwt.decode(state, jwt_secret, algorithms=["HS256"])
        except jwt.PyJWTError:
            return RedirectResponse(url="/?google=error&detail=bad_state", status_code=302)
        if data.get("purpose") != "google_oauth":
            return RedirectResponse(url="/?google=error&detail=bad_state", status_code=302)
        user_id = str(data.get("sub") or "")
        if not user_id or accounts.get(user_id) is None:
            return RedirectResponse(url="/?google=error&detail=user", status_code=302)
        try:
            tokens = exchange_code(code)
            access = str(tokens.get("access_token") or "")
            refresh = str(tokens.get("refresh_token") or "") or None
            google_tokens.save(
                user_id,
                access_token=access,
                refresh_token=refresh,
                expires_in=int(tokens.get("expires_in") or 3600),
                scope=str(tokens.get("scope") or ""),
            )
            # fetch email
            from core.google_oauth import GoogleClient

            email = GoogleClient(google_tokens, user_id).fetch_email()
            if email:
                tok = google_tokens.get(user_id)
                google_tokens.save(
                    user_id,
                    access_token=access,
                    refresh_token=refresh or (tok or {}).get("refresh_token"),
                    expires_in=int(tokens.get("expires_in") or 3600),
                    email=email,
                    scope=str(tokens.get("scope") or ""),
                )
        except Exception:  # noqa: BLE001
            return RedirectResponse(url="/?google=error&detail=token", status_code=302)
        audit.log("google_connected", user_id=user_id)
        return RedirectResponse(url="/?google=connected", status_code=302)

    @app.delete("/api/google/disconnect")
    def google_disconnect(account: Account = Depends(require_account)) -> dict[str, Any]:
        google_tokens.clear(account.id)
        return {"ok": True}

    @app.post("/api/uploads")
    async def upload_file(
        file: UploadFile = File(...),
        account: Account = Depends(require_account),
    ) -> dict[str, Any]:
        from core.vision import describe_image

        raw = await file.read()
        try:
            meta = uploads.save(
                account.id,
                filename=file.filename or "file",
                content_type=file.content_type or "application/octet-stream",
                data=raw,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        ctype = str(meta.get("content_type") or "")
        if ctype.startswith("image/") and not meta.get("text_preview"):
            key = (account.api_key or settings.openai_api_key or "").strip()
            if key:

                def _desc() -> str | None:
                    return describe_image(
                        raw,
                        content_type=ctype,
                        api_key=key,
                        base_url=account.api_base_url
                        or settings.openai_base_url
                        or "https://api.openai.com/v1",
                        model=account.model or settings.openai_model or "gpt-4o-mini",
                    )

                try:
                    preview = await asyncio.to_thread(_desc)
                except Exception:  # noqa: BLE001
                    preview = None
                if preview:
                    meta = uploads.set_text_preview(account.id, str(meta["id"]), preview) or meta
        public = {k: v for k, v in meta.items() if k != "path"}
        return {"file": public}

    @app.get("/api/uploads")
    def list_uploads(account: Account = Depends(require_account)) -> dict[str, Any]:
        rows = uploads.list(account.id)
        return {"files": [{k: v for k, v in r.items() if k != "path"} for r in rows]}

    @app.get("/api/backup")
    def download_backup(account: Account = Depends(require_owner)) -> Response:
        blob = build_backup_zip(settings.data_dir, include_secrets_key=False)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return Response(
            content=blob,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="nexus-backup-{stamp}.zip"'
            },
        )

    @app.post("/api/voice/transcribe")
    async def voice_transcribe(
        file: UploadFile = File(...),
        language: str = Form(default=""),
        account: Account = Depends(require_account),
    ) -> dict[str, Any]:
        """Whisper STT (HQ voice path)."""
        api_key = (account.api_key or settings.openai_api_key or "").strip()
        if not api_key:
            raise HTTPException(
                400,
                "Add your AI API key in Settings to use Whisper voice.",
            )
        raw = await file.read()
        if not raw:
            raise HTTPException(400, "Empty audio")
        if len(raw) > 25 * 1024 * 1024:
            raise HTTPException(400, "Audio too large (max 25MB)")
        base = (account.api_base_url or settings.openai_base_url or "https://api.openai.com/v1").rstrip(
            "/"
        )
        filename = file.filename or "voice.webm"
        lang = (language or "").strip()
        if lang and "-" in lang:
            lang = lang.split("-", 1)[0]

        def _whisper() -> str:
            import io
            import httpx

            data: dict[str, str] = {"model": "whisper-1"}
            if lang:
                data["language"] = lang
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(
                    f"{base}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={
                        "file": (
                            filename,
                            io.BytesIO(raw),
                            file.content_type or "application/octet-stream",
                        )
                    },
                    data=data,
                )
            if resp.status_code >= 400:
                raise RuntimeError(resp.text[:400] or f"HTTP {resp.status_code}")
            return str((resp.json() or {}).get("text") or "").strip()

        try:
            text = await asyncio.to_thread(_whisper)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Transcription failed: {exc}") from exc
        if not text:
            raise HTTPException(400, "No speech detected")
        return {"text": text}

    @app.post("/api/voice/speak")
    async def voice_speak(
        body: SpeakBody, account: Account = Depends(require_account)
    ) -> Response:
        """OpenAI TTS for HQ voice replies."""
        api_key = (account.api_key or settings.openai_api_key or "").strip()
        if not api_key:
            raise HTTPException(400, "Add your AI API key in Settings for HQ voice.")
        base = (account.api_base_url or settings.openai_base_url or "https://api.openai.com/v1").rstrip(
            "/"
        )
        clean = (body.text or "").strip()[:4000]
        if not clean:
            raise HTTPException(400, "Empty text")
        voice = (body.voice or "alloy").strip() or "alloy"
        # Prefer Russian-friendly voice when lang hints ru
        lang = (body.language or "").lower()
        if lang.startswith("ru") and voice == "alloy":
            voice = "nova"

        def _tts() -> bytes:
            import httpx

            with httpx.Client(timeout=120.0) as client:
                resp = client.post(
                    f"{base}/audio/speech",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "tts-1",
                        "voice": voice,
                        "input": clean,
                        "response_format": "mp3",
                    },
                )
            if resp.status_code >= 400:
                raise RuntimeError(resp.text[:400] or f"HTTP {resp.status_code}")
            return resp.content

        try:
            audio = await asyncio.to_thread(_tts)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Speech failed: {exc}") from exc
        return Response(content=audio, media_type="audio/mpeg")

    @app.get("/api/conversations")
    def list_conversations(account: Account = Depends(require_account)) -> dict[str, Any]:
        return {"conversations": chats.list_conversations(account.id)}

    @app.post("/api/conversations")
    def create_conversation(
        body: ConversationBody, account: Account = Depends(require_account)
    ) -> dict[str, Any]:
        return {"conversation": chats.create_conversation(account.id, body.title)}

    @app.patch("/api/conversations/{conversation_id}")
    def rename_conversation(
        conversation_id: str,
        body: ConversationPatch,
        account: Account = Depends(require_account),
    ) -> dict[str, Any]:
        conv = chats.rename_conversation(account.id, conversation_id, body.title)
        if not conv:
            raise HTTPException(404, "Conversation not found")
        return {"conversation": conv}

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation(
        conversation_id: str, account: Account = Depends(require_account)
    ) -> dict[str, Any]:
        ok = chats.delete_conversation(account.id, conversation_id)
        if not ok:
            raise HTTPException(404, "Conversation not found")
        key = _agent_key(account.id, conversation_id)
        with agents_guard:
            agent = agents.pop(key, None)
            if agent is not None:
                agent.reset()
        return {"ok": True}

    @app.get("/api/briefing/schedule")
    def get_briefing_schedule_api(account: Account = Depends(require_account)) -> dict[str, Any]:
        cfg = briefing_schedules.get(account.id)
        return {"schedule": cfg, "timezone": cfg.get("timezone") or "Asia/Jerusalem"}

    @app.put("/api/briefing/schedule")
    def put_briefing_schedule_api(
        body: BriefingScheduleBody, account: Account = Depends(require_account)
    ) -> dict[str, Any]:
        try:
            cfg = briefing_schedules.set(
                account.id,
                enabled=body.enabled,
                hour=body.hour,
                minute=body.minute,
                timezone_name=body.timezone or "Asia/Jerusalem",
                location=body.location or "Tel Aviv",
                evening_enabled=body.evening_enabled,
                evening_hour=body.evening_hour,
                evening_minute=body.evening_minute,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"schedule": cfg, "timezone": cfg.get("timezone") or "Asia/Jerusalem"}

    @app.get("/api/chat")
    def get_chat(
        conversation_id: str = "main",
        account: Account = Depends(require_account),
    ) -> dict[str, Any]:
        return {
            "conversation_id": conversation_id or "main",
            "messages": chats.load(account.id, conversation_id or "main"),
        }

    @app.delete("/api/chat")
    def clear_chat(
        conversation_id: str = "main",
        account: Account = Depends(require_account),
    ) -> dict[str, Any]:
        cid = conversation_id or "main"
        chats.clear(account.id, cid)
        key = _agent_key(account.id, cid)
        with agents_guard:
            agent = agents.pop(key, None)
            if agent is not None:
                agent.reset()
        return {"ok": True}

    @app.post("/api/chat")
    async def chat(
        body: ChatBody, account: Account = Depends(require_account)
    ) -> dict[str, Any]:
        text = body.message.strip()
        if not text:
            raise HTTPException(400, "Empty message")
        cid = (body.conversation_id or "main").strip() or "main"
        lock = _lock_for(account.id, cid)
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "Already working on your previous message")

        attach_bits: list[str] = []
        for fid in body.attachment_ids or []:
            meta = uploads.get(account.id, fid)
            if not meta:
                continue
            bit = f"[Attachment {meta.get('filename')} ({meta.get('content_type')}, {meta.get('bytes')} bytes)"
            if meta.get("text_preview"):
                bit += f"]\n{meta['text_preview']}"
            else:
                bit += f" stored as {meta.get('stored_name')}]"
            attach_bits.append(bit)
        agent_input = text
        if attach_bits:
            agent_input = text + "\n\n" + "\n\n".join(attach_bits)

        def run() -> str:
            try:
                agent = _agent_for(account, cid)
                device_id = account.resolve_device_id(devices.local_id)
                with request_context(user_id=account.id, device_id=device_id):
                    return agent.run(agent_input)
            finally:
                lock.release()

        chats.append(
            account.id,
            "user",
            text + (f" (+{len(attach_bits)} file(s))" if attach_bits else ""),
            cid,
        )
        try:
            reply = await asyncio.to_thread(run)
        except Exception:
            chats.append(account.id, "system", "Chat failed — try again.", cid)
            raise
        chats.append(account.id, "assistant", reply or "(empty)", cid)
        return {"reply": reply, "conversation_id": cid}

    @app.post("/api/confirm")
    def confirm(
        body: ConfirmBody, account: Account = Depends(require_account)
    ) -> dict[str, Any]:
        ok = broker.resolve(body.id, account.id, body.allowed)
        if not ok:
            raise HTTPException(404, "Confirmation not found or expired")
        return {"ok": True}

    @app.get("/api/push/vapid")
    def push_vapid(account: Account = Depends(require_account)) -> dict[str, Any]:
        _ = account
        return {"publicKey": push.public_b64}

    @app.post("/api/push/subscribe")
    def push_subscribe(
        body: PushSubBody, account: Account = Depends(require_account)
    ) -> dict[str, Any]:
        try:
            push.add(account.id, {"endpoint": body.endpoint, "keys": body.keys})
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @app.delete("/api/push/subscribe")
    def push_unsubscribe(
        body: PushSubBody, account: Account = Depends(require_account)
    ) -> dict[str, Any]:
        push.remove(account.id, body.endpoint)
        return {"ok": True}

    @app.websocket("/api/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        token = websocket.query_params.get("token") or ""
        try:
            data = jwt.decode(token, jwt_secret, algorithms=["HS256"])
            account = accounts.get(str(data.get("sub") or ""))
            if account is None:
                await websocket.close(code=4401)
                return
        except jwt.PyJWTError:
            await websocket.close(code=4401)
            return

        queue = broker.subscribe(account.id)
        try:
            await websocket.send_json({"type": "hello", "user": account.public_dict()})

            async def reader() -> None:
                while True:
                    msg = await websocket.receive_json()
                    if isinstance(msg, dict) and msg.get("type") == "confirm_reply":
                        broker.resolve(
                            str(msg.get("id") or ""),
                            account.id,
                            bool(msg.get("allowed")),
                        )

            async def writer() -> None:
                while True:
                    payload = await queue.get()
                    await websocket.send_json(payload)

            await asyncio.gather(reader(), writer())
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            broker.unsubscribe(account.id, queue)

    # Reminder / briefing pings → in-app via audit + optional WS notify
    def _send_reminder(user_id: str, text: str) -> bool:
        live = broker.notify(user_id, {"type": "reminder", "text": text})
        pushed = push.send(user_id, "Nexus Node", text)
        if live or pushed:
            chats.append(user_id, "system", text, "main")
            audit.log(
                "reminder_delivered",
                user_id=user_id,
                text=text[:120],
                ws=live,
                push=pushed,
            )
            return True
        # Nobody listening and no push subscription — retry next tick.
        return False

    def _send_briefing(user_id: str, text: str, title: str = "Morning briefing") -> bool:
        live = broker.notify(
            user_id, {"type": "briefing", "text": text, "title": title}
        )
        pushed = push.send(user_id, title or "Briefing", text[:180])
        if live or pushed:
            chats.append(user_id, "system", text, "main")
            audit.log(
                "briefing_delivered",
                user_id=user_id,
                text=text[:120],
                title=title,
                ws=live,
                push=pushed,
            )
            return True
        return False

    notifier = ReminderNotifier(
        daily_root=settings.data_dir / "daily",
        send=_send_reminder,
        interval_seconds=30.0,
    )
    notifier.start()
    briefing_notifier = BriefingNotifier(
        briefing_schedules,
        send=_send_briefing,
        interval_seconds=60.0,
    )
    briefing_notifier.start()

    @app.on_event("shutdown")
    def _shutdown() -> None:
        notifier.stop()
        briefing_notifier.stop()

    # Static UI (production build)
    web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    if web_dist.is_dir():
        assets = web_dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}")
        def spa(full_path: str) -> FileResponse:
            candidate = web_dist / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(web_dist / "index.html")

    app.state.nexus = {  # type: ignore[attr-defined]
        "accounts": accounts,
        "devices": devices,
        "broker": broker,
        "settings": settings,
    }
    return app


def _load_or_create_secret(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(48)
    path.write_text(value + "\n", encoding="utf-8")
    return value


def run_web_server(
    settings: Settings,
    registry: ToolRegistry,
    *,
    host: str = "0.0.0.0",
    port: int = 8743,
    tls: bool | None = None,
) -> None:
    import uvicorn

    if tls is None:
        tls = os.getenv("NEXUS_WEB_TLS", "").strip().lower() in {"1", "true", "yes", "on"}
    ssl_kwargs: dict[str, str] = {}
    if tls:
        from core.tls import ensure_dev_cert

        cert, key = ensure_dev_cert(settings.data_dir)
        ssl_kwargs = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
        print(f"TLS cert: {cert}")

    app = create_app(
        settings,
        registry,
        tls=bool(tls),
        bind_host=host,
        bind_port=port,
    )
    print_hub_banner(host, port, tls=bool(tls))
    uvicorn.run(app, host=host, port=port, log_level="info", **ssl_kwargs)
