"""第一批管理接口：认证、基础配置、会话安全及真实运行状态。"""
import asyncio
import json
import platform
from time import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import __version__
from ..security import AuthenticationError, Session, TooManyAttempts, csrf_token
from ..storage.configuration import PreferencesUpdate, RevisionConflict
from ..storage.database import SCHEMA_VERSION


router = APIRouter(prefix="/api/v1")


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128, repr=False)


class PasswordInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    current_password: str = Field(min_length=1, max_length=128, repr=False)
    new_password: str = Field(min_length=12, max_length=128, repr=False)


def current_session(request: Request) -> Session:
    try:
        return request.app.state.runtime.auth.authenticate(request.cookies.get(request.app.state.settings.cookie_name))
    except AuthenticationError as exc:
        raise HTTPException(401, str(exc)) from exc


def write_session(request: Request, session: Session = Depends(current_session)) -> Session:
    try:
        request.app.state.runtime.auth.csrf(session, request.headers.get("x-rp-csrf"))
    except AuthenticationError as exc:
        raise HTTPException(403, str(exc)) from exc
    return session


@router.post("/auth/login")
async def login(body: LoginInput, request: Request, response: Response):
    runtime, settings = request.app.state.runtime, request.app.state.settings
    try:
        user, token, csrf = await runtime.auth.login(
            body.username, body.password, request.client.host if request.client else "unknown",
            request.headers.get("user-agent", ""))
    except TooManyAttempts as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "60"}) from exc
    except AuthenticationError as exc:
        raise HTTPException(401, str(exc)) from exc
    response.set_cookie(settings.cookie_name, token, max_age=settings.session_seconds,
                        httponly=True, secure=settings.cookie_secure, samesite="strict", path="/")
    runtime.events.publish("security")
    return {"user": user, "csrf_token": csrf}


@router.get("/auth/session")
async def session_info(request: Request, session: Session = Depends(current_session)):
    token = request.cookies[request.app.state.settings.cookie_name]
    return {"user": {"id": session.id, "username": session.username, "expires_at": session.expires_at},
            "csrf_token": csrf_token(token)}


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, session: Session = Depends(write_session)):
    request.app.state.runtime.auth.logout(session)
    request.app.state.runtime.events.publish("security")
    response = Response(status_code=204)
    response.delete_cookie(request.app.state.settings.cookie_name, path="/",
                           secure=request.app.state.settings.cookie_secure, httponly=True, samesite="strict")
    return response


@router.get("/auth/sessions")
async def list_sessions(request: Request, session: Session = Depends(current_session)):
    runtime, settings = request.app.state.runtime, request.app.state.settings
    rows = runtime.database.all("""SELECT id,created_at,last_seen_at,expires_at,peer,user_agent
        FROM login_sessions WHERE expires_at>? AND last_seen_at>? ORDER BY created_at DESC""",
                                (time(), time() - settings.session_idle_seconds))
    return [{**row, "current": row["id"] == session.id} for row in rows]


@router.delete("/auth/sessions/others")
async def revoke_sessions(request: Request, session: Session = Depends(write_session)):
    count = request.app.state.runtime.auth.revoke_others(session)
    request.app.state.runtime.events.publish("security")
    return {"revoked": count}


@router.post("/auth/password", status_code=204)
async def change_password(body: PasswordInput, request: Request, session: Session = Depends(write_session)):
    runtime = request.app.state.runtime
    try:
        await runtime.auth.change_password(session, body.current_password, body.new_password)
    except TooManyAttempts as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "60"}) from exc
    except AuthenticationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    runtime.events.publish("security")
    response = Response(status_code=204)
    response.delete_cookie(request.app.state.settings.cookie_name, path="/",
                           secure=request.app.state.settings.cookie_secure, httponly=True, samesite="strict")
    return response


@router.get("/system/preferences")
async def preferences(request: Request, _: Session = Depends(current_session)):
    return request.app.state.runtime.configuration.preferences()


@router.put("/system/preferences")
async def update_preferences(body: PreferencesUpdate, request: Request, _: Session = Depends(write_session)):
    try:
        result = request.app.state.runtime.configuration.save_preferences(body)
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    request.app.state.runtime.events.publish("preferences")
    return result


@router.get("/system/status")
async def status(request: Request, _: Session = Depends(current_session)):
    runtime, settings = request.app.state.runtime, request.app.state.settings
    now = time()
    sessions = runtime.database.one("SELECT count(*) AS count FROM login_sessions WHERE expires_at>? AND last_seen_at>?",
                                    (now, now - settings.session_idle_seconds))["count"]
    return {
        "version": __version__, "started_at": runtime.started_at, "server_time": now,
        "uptime_seconds": max(0, int(now - runtime.started_at)), "phase": "RP 独立管理服务",
        "database": {"engine": "SQLite", "schema_version": SCHEMA_VERSION, "journal_mode": "wal"},
        "active_admin_sessions": sessions, "event_connections": runtime.events.connections,
        "credentials": {"encrypted": True, "key_source": runtime.key_source},
        "static_ready": (settings.static_dir / "index.html").is_file(),
        "capabilities": {"administration": True, "model_connections": True, "qq": True, "roleplay": True},
        "roleplay": {**runtime.roleplay.summary(), "enabled": runtime.rp_store.settings()['enabled']},
        "models": runtime.models.summary(),
        "qq": {"accounts": len(runtime.qq_accounts.list()),
               "connected": sum(1 for connection in runtime.qq.connections.values() if connection.accepting),
               "instances": runtime.database.one('SELECT count(*) AS count FROM role_instances')['count'],
               "received": runtime.database.one('SELECT count(*) AS count FROM inbound_messages WHERE deleted_at IS NULL AND archived_at IS NULL')['count'],
               "images": runtime.inbound_images.summary()},
    }


@router.get("/system/info")
async def system_info(request: Request, _: Session = Depends(current_session)):
    settings = request.app.state.settings
    return {"version": __version__, "python": platform.python_version(), "platform": platform.system(),
            "data_dir": str(settings.data_dir), "public_origin": settings.public_origin,
            "allowed_origins": sorted(settings.allowed_origins), "cookie_secure": settings.cookie_secure,
            "session_seconds": settings.session_seconds, "session_idle_seconds": settings.session_idle_seconds,
            "database_schema": SCHEMA_VERSION}


@router.get("/audit")
async def audit(request: Request, before: int | None = None, _: Session = Depends(current_session)):
    if before is not None and before < 1:
        raise HTTPException(400, "分页标识无效。")
    rows = request.app.state.runtime.database.all(
        "SELECT * FROM audit_events WHERE id<? ORDER BY id DESC LIMIT 51", (before or 9223372036854775807,))
    return {"items": [{**{key: value for key, value in row.items() if key != "details_json"},
                       "details": json.loads(row["details_json"])} for row in rows[:50]],
            "next_before": rows[49]["id"] if len(rows) > 50 else None}


@router.get("/events")
async def events(request: Request, _: Session = Depends(current_session)):
    runtime, settings = request.app.state.runtime, request.app.state.settings
    if runtime.events.connections >= 32:
        raise HTTPException(429, "事件连接过多，请关闭重复打开的管理页。")
    token = request.cookies.get(settings.cookie_name)

    async def stream():
        async with runtime.events.subscribe() as queue:
            yield 'event: refresh\ndata: {"kind":"refresh"}\n\n'
            while not await request.is_disconnected():
                try:
                    # SSE 心跳不延长管理员的闲置会话。
                    runtime.auth.authenticate(token, touch=False)
                except AuthenticationError:
                    yield 'event: expired\ndata: {}\n\n'
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield f"id: {event['id']}\nevent: refresh\ndata: {json.dumps(event)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"})
