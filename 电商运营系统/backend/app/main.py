"""FastAPI application assembly: middleware, routers, static page and startup.

This is the single official entry point of the system (``python run.py`` or
``uvicorn app.main:app``). The legacy ``server.py`` adapter is kept only for historical
comparison and is no longer the default service.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core import config, errors
from app import db
from app.api import deps
from app.api.routers import ai, analytics, auth, forecast, imports, inventory, system

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
log = logging.getLogger('ece.main')

EXEMPT_PREFIXES = ('/api/health', '/api/auth/login', '/api/auth/logout', '/docs', '/redoc', '/openapi.json')
COOKIE_PATH = '/'


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.ensure_dirs()
    db.init_app_db()
    with db.connect() as cx:
        stamp = db.now_iso()
        cx.execute("update jobs set status='failed', error='服务重启，任务中断', finished_at=? "
                   "where status in ('queued','running')", (stamp,))
        cx.execute("update import_jobs set status='failed', error='服务重启，导入中断', finished_at=? "
                   "where status in ('queued','running')", (stamp,))
    log.info('startup complete; app db=%s', config.APP_DB)
    yield
    log.info('shutdown')


app = FastAPI(
    title='智能电商运营管理系统（课程原型）',
    description='登录、数据导入、看板与订单、全店预测、模拟库存、AI查询与简报。上线前需替换初始化口令与AI密钥。',
    version=config.APP_VERSION,
    lifespan=lifespan,
)
errors.register(app)


@app.middleware('http')
async def session_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith('/api/'):
        # Same-origin write guard, mirroring the previous local adapter.
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            host = request.headers.get('host', '')
            if origin and origin not in (f'http://{host}', f'https://{host}'):
                return JSONResponse(status_code=403, content={
                    'error': '仅接受本地同源页面的请求', 'code': 'forbidden'})
        if not path.startswith(EXEMPT_PREFIXES):
            user = deps.user_from_token(request.cookies.get(config.COOKIE_NAME))
            request.state.user = user
            if not user:
                return JSONResponse(status_code=401, content={
                    'error': '未登录或登录已失效', 'code': 'unauthorized'})
    return await call_next(request)


app.include_router(system.router, prefix='/api')
app.include_router(auth.router, prefix='/api')
app.include_router(analytics.router, prefix='/api')
app.include_router(imports.router, prefix='/api')
app.include_router(forecast.router, prefix='/api')
app.include_router(inventory.router, prefix='/api')
app.include_router(ai.router, prefix='/api')


# Vue 3 + Element Plus build output takes precedence over the legacy pages when present.
FRONTEND = config.FRONTEND_DIST if (config.FRONTEND_DIST / 'index.html').is_file() else None
if FRONTEND is not None:
    assets = FRONTEND / 'assets'
    if assets.is_dir():
        app.mount('/assets', StaticFiles(directory=assets), name='frontend-assets')


@app.get('/', include_in_schema=False)
def index():
    if FRONTEND is not None:
        return FileResponse(FRONTEND / 'index.html', media_type='text/html; charset=utf-8')
    return JSONResponse(status_code=503, content={'error': '请先构建frontend目录下的Vue页面', 'code': 'not_ready'})


@app.get('/favicon.ico', include_in_schema=False)
def favicon():
    icon = config.FRONTEND_DIST / 'favicon.ico'
    if icon.is_file():
        return FileResponse(icon)
    return JSONResponse(status_code=204, content=None)


@app.get('/{spa_path:path}', include_in_schema=False)
def spa_fallback(spa_path: str):
    """History-mode router fallback: unknown non-API paths return the SPA shell.

    Unknown ``/api/*`` paths must stay JSON 404 (interface contract), never the page.
    """
    if spa_path == 'api' or spa_path.startswith('api/'):
        return JSONResponse(status_code=404, content={'error': '路径不存在', 'code': 'not_found'})
    if FRONTEND is not None:
        candidate = FRONTEND / spa_path
        if spa_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND / 'index.html', media_type='text/html; charset=utf-8')
    return JSONResponse(status_code=404, content={'error': '路径不存在', 'code': 'not_found'})
