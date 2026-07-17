"""FlowCut API Server

Endpoints:
  POST /agent/chat          — SSE 流式对话
  GET/POST/PATCH/DELETE /materials/*  — 素材管理
  GET/PATCH /creatives/*    — 成片管理
  GET/POST /qianchuan/*     — 千川账号管理
  GET /health               — 健康检查
"""
from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from Flowcut.api.container import build_container
from Flowcut.config import make_cors_origins
from Flowcut.storage.oss_client import build_oss_client
from Flowcut.api.routes.auth import router as auth_router
from Flowcut.api.routes.chat import router as chat_router
from Flowcut.api.routes.creatives import router as creatives_router
from Flowcut.api.routes.health import router as health_router
from Flowcut.api.routes.highlight_assets import router as highlight_assets_router
from Flowcut.api.routes.highlight_batches import router as highlight_batches_router
from Flowcut.api.routes.materials import router as materials_router
from Flowcut.api.routes.qianchuan import router as qianchuan_router
from Flowcut.api.routes.reference_videos import router as ref_videos_router
from Flowcut.api.routes.scripts import router as scripts_router
from Flowcut.api.routes.sessions import router as sessions_router
from Flowcut.api.routes.tasks import router as tasks_router

app = FastAPI(title="FlowCut Agent MVP")
app.add_middleware(
    CORSMiddleware,
    allow_origins=make_cors_origins(),   # cookie 鉴权要求具体来源，不能用 "*"
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(materials_router)
app.include_router(highlight_assets_router)
app.include_router(creatives_router)
app.include_router(qianchuan_router)
app.include_router(ref_videos_router)
app.include_router(scripts_router)
app.include_router(sessions_router)
app.include_router(tasks_router)
app.include_router(highlight_batches_router)
app.include_router(health_router)


@app.on_event("startup")
async def startup() -> None:
    build_oss_client().ensure_cors()   # 写入 bucket CORS 规则，允许浏览器直传
    container = await build_container()
    app.state.container = container
    logger.info("FlowCut 启动完成")


@app.on_event("shutdown")
async def shutdown() -> None:
    container = getattr(app.state, "container", None)
    if container is None:
        return
    for t in container.worker_tasks:
        t.cancel()
    if container.worker_tasks:
        await asyncio.gather(*container.worker_tasks, return_exceptions=True)
    if container.db:
        await container.db.close()
        logger.info("MySQL 已断开")
