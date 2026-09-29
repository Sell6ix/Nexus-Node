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
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.accounts import Account, AccountStore
from core.agent import Agent
from core.audit import get_audit_logger
from core.config import Settings
from core.context import request_context
from core.devices import DeviceRegistry, ping_device
from core.netinfo import hub_urls, print_hub_banner
from core.permissions import Role
from core.push import PushStore
from core.registry import ToolRegistry
from core.reminders import ReminderNotifier
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
) -> FastAPI:
    open_signup = (
        open_signup
        if open_signup is not None
        else os.getenv("NEXUS_OPEN_SIGNUP", "").strip().lower() in {"1", "true", "yes", "on"}
    )
    jwt_secret = os.getenv("NEXUS_JWT_SECRET") or _load_or_create_secret(
        settings.data_dir / "jwt_secret.txt"
    )
    accounts = AccountStore(
        settings.data_dir / "accounts.json",
        max_members=int(os.getenv("NEXUS_MAX_MEMBERS", "50")),
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
    agent_locks: dict[str, threading.Lock] = {}
    agent_locks_guard = threading.Lock()

    app = FastAPI(title="Nexus Node", version="0.2.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _lock_for(user_id: str) -> threading.Lock:
        with agent_locks_guard:
            lock = agent_locks.get(user_id)
            if lock is None:
                lock = threading.Lock()
                agent_locks[user_id] = lock
            return lock

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
        port = int(os.getenv("NEXUS_WEB_PORT", "8743"))
        host = os.getenv("NEXUS_WEB_HOST", "0.0.0.0")
        return {
            "ok": True,
            "product": "Nexus Node",
            "device": settings.device_id,
            "has_owner": accounts.has_owner,
            "open_signup": open_signup or not accounts.has_owner,
            "tools": len(registry),
            "urls": hub_urls(host, port),
            "push": True,
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

    @app.get("/api/me")
    def me(account: Account = Depends(require_account)) -> dict[str, Any]:
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

    @app.post("/api/chat")
    async def chat(
        body: ChatBody, account: Account = Depends(require_account)
    ) -> dict[str, Any]:
        lock = _lock_for(account.id)
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "Already working on your previous message")

        def run() -> str:
            try:
                role = Role.OWNER if account.role == "owner" else Role.FRIEND
                device_id = account.resolve_device_id(devices.local_id)

                def confirm_fn(prompt: str, _use_tg: bool) -> bool:
                    # Member confirms own actions; dangerous friend tools still ACL-gated.
                    return broker.ask(account.id, prompt)

                agent = Agent(
                    registry,
                    settings,
                    confirm_fn=confirm_fn,
                    audit=audit,
                    source=f"web:{account.id}",
                    role=role,
                    requester_id=account.id,
                    user_profile=_account_to_profile(account),
                    devices=devices,
                    active_device_id=device_id,
                )
                with request_context(user_id=account.id, device_id=device_id):
                    return agent.run(body.message.strip())
            finally:
                lock.release()

        reply = await asyncio.to_thread(run)
        return {"reply": reply}

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

    # Reminder pings → in-app via audit + optional WS notify to that user id
    def _send_reminder(user_id: str, text: str) -> bool:
        live = broker.notify(user_id, {"type": "reminder", "text": text})
        pushed = push.send(user_id, "Nexus Node", text)
        if live or pushed:
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

    notifier = ReminderNotifier(
        daily_root=settings.data_dir / "daily",
        send=_send_reminder,
        interval_seconds=30.0,
    )
    notifier.start()

    @app.on_event("shutdown")
    def _shutdown() -> None:
        notifier.stop()

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
) -> None:
    import uvicorn

    app = create_app(settings, registry)
    print_hub_banner(host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
