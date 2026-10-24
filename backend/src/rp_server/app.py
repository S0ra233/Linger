"""应用生命周期拥有数据库及事件服务，不在导入时启动业务。"""
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
import os
from pathlib import Path
from time import time
from traceback import extract_tb
from uuid import uuid4

import httpx
from .llm.gemini_proxy import GeminiProxy

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.management import router
from .api.body_limit import ManagementBodyLimit
from .api.models import router as models_router
from .api.roleplay import router as roleplay_router, gateway_router
from .api.runtime import router as runtime_router
from .api.materials import router as materials_router
from .api.presets import router as presets_router
from .api.context_strategies import router as context_strategies_router
from .api.information import router as information_router
from .api.mcp import router as mcp_router
from .api.life import router as life_router
from .api.maintenance import router as maintenance_router
from .api.usage import router as usage_router
from .api.media import router as media_router
from .attachments.media import MediaAssets
from .attachments.inbound import InboundImages
from .attachments.store import ImageStore
from .information.characters import Characters
from .information.worldbooks import Worldbooks
from .information.imports import MaterialImports
from .information.semantic import SemanticInformation
from .information.service import Information
from .information.adjustments import Adjustments
from .roleplay.context import ContextService
from .integrations.onebot import OneBotGateway
from .integrations.qq_accounts import QQAccounts
from .integrations.mcp_service import McpService
from .roleplay.inputs import InputService
from .roleplay.store import RpStore
from .roleplay.runtime import RpRuntime
from .roleplay.life import LifeService
from .roleplay.extensions import LifeExtensions
from .llm.service import ModelService
from .llm.cache_maintenance import CacheMaintenance
from .llm.explicit_cache import ExplicitCaches
from .events import EventBus
from .security import AuthService
from .settings import ServerSettings, parse_origin
from .storage.configuration import Configuration
from .storage.credentials import Credentials
from .storage.database import Database
from .storage.process_lock import ProcessLock
from .storage.backups import Backups


logger = logging.getLogger("rp_server")


@dataclass
class Runtime:
    database: Database
    credentials: Credentials
    configuration: Configuration
    auth: AuthService
    events: EventBus
    started_at: float
    key_source: str
    models: ModelService
    characters: Characters
    qq_accounts: QQAccounts
    inputs: InputService
    inbound_images: InboundImages
    qq: OneBotGateway
    rp_store: RpStore
    roleplay: RpRuntime
    worldbooks: Worldbooks
    material_imports: MaterialImports
    context: ContextService
    information: Information
    semantic: SemanticInformation
    mcp: McpService
    life: LifeService
    backups: Backups


def create_app(settings: ServerSettings | None = None) -> FastAPI:
    settings = settings or ServerSettings.from_env()
    # httpx 的 INFO 会输出完整 URL；QQ 图片查询参数含临时下载凭据。
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with ProcessLock(settings.data_dir):
            database = Database(settings.database_path)
            try:
                if database.one("SELECT id FROM administrator WHERE id=1") is None:
                    raise ValueError("管理员尚未初始化，请先运行 rp-server init。")
                credentials = Credentials(database, settings)
                credentials.check_key()
                configuration = Configuration(database)
                configuration.preferences()
                events = EventBus()
                async with httpx.AsyncClient(follow_redirects=False, limits=httpx.Limits(
                        max_connections=8, max_keepalive_connections=4)) as client, GeminiProxy.from_env() as gemini_proxy:
                    images = ImageStore(settings.data_dir / 'attachments')
                    models = ModelService(database, credentials, events, client, images, gemini_proxy=gemini_proxy)
                    models.presets.ensure_default()
                    characters = Characters(database, events)
                    characters.synchronize()
                    qq_accounts = QQAccounts(database, credentials, events, settings.public_origin)
                    inputs = InputService(database, events)
                    inbound_images = InboundImages(database, events, client, images)
                    qq = OneBotGateway(qq_accounts, inputs, events, inbound_images.wake)
                    rp_store = RpStore(database, events)
                    rp_store.cache = CacheMaintenance(rp_store, models)
                    rp_store.media = MediaAssets(rp_store, credentials, images)
                    life = LifeService(rp_store, qq)
                    rp_store.life = life
                    mcp = McpService(rp_store, credentials)
                    worldbooks = Worldbooks(database, events, characters)
                    rp_store.worldbooks = worldbooks
                    material_imports = MaterialImports(database, events, settings.data_dir / 'imports', characters, worldbooks)
                    information = Information(rp_store)
                    semantic = SemanticInformation(information, models)
                    information.semantic = semantic
                    rp_store.information = information
                    rp_store.adjustments = Adjustments(rp_store)
                    rp_store.extensions = LifeExtensions(rp_store, client)
                    rp_store.explicit_cache = ExplicitCaches(rp_store, models)
                    context = ContextService(rp_store, models, worldbooks, information, mcp)
                    roleplay = RpRuntime(rp_store, models, qq, context)
                    backups = Backups(database, events, settings)
                    app.state.runtime = Runtime(database, credentials, configuration,
                                                AuthService(database, settings), events, time(),
                                                "environment" if os.environ.get("RP_MASTER_KEY") else "file", models,
                                                characters, qq_accounts, inputs, inbound_images, qq, rp_store, roleplay,
                                                worldbooks, material_imports, context, information, semantic, mcp, life, backups)
                    try:
                        inbound_images.start()
                        roleplay.start()
                        logger.info("RP 服务已启动，版本 %s。", __version__)
                        yield
                    finally:
                        # 所有使用共享 HTTP／数据库的任务先退出，才释放底层资源。
                        try:
                            try:
                                await backups.close()
                            finally:
                                await roleplay.close()
                        finally:
                            try:
                                await mcp.close()
                            finally:
                                try:
                                    await qq.close()
                                finally:
                                    try:
                                        await inbound_images.close()
                                    finally:
                                        try:
                                            await semantic.close()
                                        finally:
                                            await models.close()
            finally:
                # 后续批次的 HTTP、调度、MCP 也由这里统一关闭。
                app.state.runtime = None
                database.close()

    app = FastAPI(title="RP 管理服务", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        request_id = uuid4().hex
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin", "")
            try:
                trusted = parse_origin(origin) in settings.allowed_origins
            except ValueError:
                trusted = False
            if not trusted:
                return JSONResponse({"detail": "请求来源不在管理端允许范围内。", "request_id": request_id}, status_code=403)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        # Pydantic 默认错误含 input；密码和未来的 API 密钥不得原样回显。
        return JSONResponse({"detail": [{"location": list(item["loc"]), "message": item["msg"],
                                         "type": item["type"]} for item in exc.errors()]}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception):
        reference = uuid4().hex
        # 只输出类型和编号，避免异常文本中带出配置或敏感字段。
        frames = [f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
                  for frame in extract_tb(exc.__traceback__)[-8:]]
        logger.error("请求异常 %s，类型 %s，位置 %s", reference, type(exc).__name__, " → ".join(frames))
        return JSONResponse({"detail": "服务处理失败，请查看运行日志编号。", "request_id": reference}, status_code=500)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    app.include_router(router)
    app.include_router(models_router)
    app.include_router(presets_router)
    app.include_router(context_strategies_router)
    app.include_router(information_router)
    app.include_router(mcp_router)
    app.include_router(life_router)
    app.include_router(maintenance_router)
    app.include_router(usage_router)
    app.include_router(media_router)
    app.include_router(roleplay_router)
    app.include_router(runtime_router)
    app.include_router(materials_router)
    app.include_router(gateway_router)
    if (settings.static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=settings.static_dir / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    async def index():
        path = settings.static_dir / "index.html"
        if path.is_file():
            return FileResponse(path, headers={"Cache-Control": "no-cache"})
        return HTMLResponse("""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
            <title>RP 管理端尚未构建</title><body><h1>后端已启动，管理端尚未构建</h1>
            <p>请在 web 目录执行 npm install 和 npm run build，然后重启服务。</p>
            <p>也可以使用项目根目录的 安装依赖.bat 完成初次准备。</p></body></html>""", status_code=503)

    app.add_middleware(ManagementBodyLimit)
    return app
