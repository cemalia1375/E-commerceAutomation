"""Mojing API Server — V3 兼容 V2 接口规范

Endpoints:
  POST /agent/chat          — 小程序后端，魔镜自定义 SSE 格式
  POST /v1/chat/completions — 火山硬件设备，OpenAI 兼容 SSE 格式
  GET  /admin               — Dev 调试页面
  GET  /health              — 健康检查
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from loguru import logger

from Mojing.api.container import build_container
from Mojing.api.routes.chat import router as chat_router
from Mojing.api.routes.health import router as health_router
from Mojing.api.routes.journey import router as journey_router

app = FastAPI(title="Mojing Agent V3")
app.include_router(chat_router)
app.include_router(journey_router)
app.include_router(health_router)


@app.on_event("startup")
async def startup() -> None:
    container = await build_container()
    app.state.container = container

    # Admin 路由需要 app 实例，故在此动态挂载（所有 repo 已就绪）
    from admin.routes import make_admin_router
    from Mojing.config import _WORKSPACE

    _subagent_prompt_dir = Path(__file__).parent.parent / "subagent" / "prompt"
    app.include_router(make_admin_router(
        workspace=_WORKSPACE,
        subagent_prompt=_subagent_prompt_dir,
        db=container.db,
        document_repo=container.doc_repo,
        session_repo=container.session_repo,
        tenant_state_repo=container.tenant_state_repo,
        runtime_task_repo=container.runtime_task_repo,
        llm=container.llm,
        main_agent=container.main_agent,
        skin_diary_subagent=container.skin_diary_subagent,
        deep_report_subagent=container.deep_report_subagent,
    ))
    from admin.lab.routes import make_lab_router
    app.include_router(make_lab_router())
    logger.info("Admin 路由已挂载：/admin/editor /admin/scenario /admin/lab")


@app.on_event("shutdown")
async def shutdown() -> None:
    container = getattr(app.state, "container", None)
    if container is None:
        return

    for t in container.worker_tasks:
        t.cancel()
    if container.worker_tasks:
        await asyncio.gather(*container.worker_tasks, return_exceptions=True)
        logger.info("TaskWorkers 已停止")

    if container.cron_scheduler:
        await container.cron_scheduler.stop()

    if container.db:
        await container.db.close()
        logger.info("MySQL 已断开")
