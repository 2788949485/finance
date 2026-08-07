"""FinanceCrew 后端 API 入口。

启动: uvicorn app.main:app --host 0.0.0.0 --port 8000

路由按功能拆分到 app/routes/ 目录，这里只保留：
- app 创建、中间件、CORS、生命周期
- 共享依赖（get_current_user / require_admin）
- 路由注册
- 前端静态托管
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from .logger import setup_logging, get_logger

# 初始化日志系统
setup_logging()
logger = get_logger("main")

app = FastAPI(title="FinanceCrew API", version="0.4.0")


@app.on_event("startup")
def _startup():
    # 数据库索引迁移
    try:
        from .db_migrations import run_migrations
        run_migrations()
    except Exception as e:
        logger.warning("数据库迁移失败: %s", e)
    # 调度器
    try:
        from .scheduler import start_scheduler
        start_scheduler()
    except Exception as e:
        logger.warning("调度器启动失败: %s", e)


@app.on_event("shutdown")
def _shutdown_scheduler():
    try:
        from .scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass


# CORS: 只允许本机和局域网（收紧安全）
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000", "http://127.0.0.1:8000",
        "http://localhost:5173", "http://127.0.0.1:5173",
    ],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
    allow_credentials=True,
)

# ---------- 请求频率限制中间件（反爬） ----------

_rate_map: dict[str, list[float]] = defaultdict(list)
RATE_WINDOW = 60  # 60秒窗口
RATE_MAX = 200    # 每窗口最多200次请求


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """全局频率限制：每IP每60秒最多200次请求，超过返回429。"""
    if request.url.path == "/api/health":
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    _rate_map[client_ip] = [t for t in _rate_map[client_ip] if now - t < RATE_WINDOW]
    if len(_rate_map[client_ip]) >= RATE_MAX:
        return JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试"})
    _rate_map[client_ip].append(now)
    return await call_next(request)


# ---------- 认证依赖（从 deps.py 导入，避免循环导入） ----------

from .deps import get_current_user, require_admin


# ---------- 注册路由模块 ----------

from .routes import (
    system, auth as auth_routes, analysis, market, portfolio,
    backtest, alerts, chat, scheduler, thesis, knowledge,
    reflection, market_data,
)

app.include_router(system.router)
app.include_router(auth_routes.router)
app.include_router(analysis.router)
app.include_router(market.router)
app.include_router(portfolio.router)
app.include_router(backtest.router)
app.include_router(alerts.router)
app.include_router(chat.router)
app.include_router(scheduler.router)
app.include_router(thesis.router)
app.include_router(knowledge.router)
app.include_router(reflection.router)
app.include_router(market_data.router)


# ---------- 前端静态托管 ----------

FRONTEND_DIST = Path(os.environ.get(
    "FRONTEND_DIST",
    Path(__file__).resolve().parent.parent.parent / "frontend" / "dist",
))

if FRONTEND_DIST.exists():
    from starlette.middleware.base import BaseHTTPMiddleware

    class NoCacheHtmlMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            resp = await call_next(request)
            if request.url.path == '/' or request.url.path.endswith('.html'):
                resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            return resp

    app.add_middleware(NoCacheHtmlMiddleware)
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
