"""/admin/lab 路由：历史照片回填 + memory 监控快照。

依赖在请求时从 request.app.state.container 取（学 /admin/scenario/run），
工厂零参数；聊天面板直接走既有 POST /agent/chat，不在此重复。
"""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from loguru import logger

from admin.lab.backfill import (
    BACKFILL_JOBS,
    BackfillRunner,
    backfill_dates,
    extract_photos,
    new_job_state,
)
from admin.lab.imagegen import (
    IMAGEGEN_JOBS,
    get_image,
    get_imagegen_photos,
    get_imagegen_zip,
    get_spec_defaults,
    list_done_jobs,
    new_imagegen_job,
    run_generation,
)
from admin.lab.memory_view import memory_snapshot
from admin.lab.tos_uploader import TosUploader, TosUploaderError

_USER_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def make_lab_router() -> APIRouter:
    router = APIRouter(prefix="/admin/lab", tags=["admin-lab"])

    @router.get("", response_class=HTMLResponse)
    async def lab_page() -> HTMLResponse:
        from admin.lab.lab_page import LAB_HTML_PAGE

        return HTMLResponse(LAB_HTML_PAGE)

    @router.post("/backfill")
    async def start_backfill(
        request: Request,
        file: UploadFile = File(...),
        user_id: str = Form(...),
    ) -> JSONResponse:
        container = getattr(request.app.state, "container", None)
        if container is None:
            return JSONResponse({"error": "服务尚未完成启动，请稍后重试"}, status_code=503)

        user_id = (user_id or "").strip()
        if not _USER_ID_RE.match(user_id):
            return JSONResponse(
                {"error": "user_id 只能包含字母/数字/下划线/连字符（1-64 位）"},
                status_code=400,
            )

        from Mojing.config import (
            make_image_analysis_url,
            make_lab_backfill_profile_timeout_s,
            make_tos_config,
        )

        uploader = TosUploader(make_tos_config())
        if not uploader.configured:
            return JSONResponse(
                {"error": "TOS 未配置：请在 .env 中填写 TOS_ACCESS_KEY / TOS_SECRET_KEY"},
                status_code=503,
            )

        try:
            zip_bytes = await file.read()
            photos = extract_photos(zip_bytes)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        days = backfill_dates(len(photos))
        state = new_job_state(user_id=user_id, photos=photos, days=days)

        from Mojing.runtime.executors import make_image_analysis_executor

        runner = BackfillRunner(
            uploader=uploader,
            image_repo=container.image_repo,
            skin_profile_repo=container.skin_profile_repo,
            runtime=container.runtime,
            analysis_execute=make_image_analysis_executor(
                make_image_analysis_url(),
                container.image_repo,
            ),
            profile_timeout_s=float(make_lab_backfill_profile_timeout_s()),
        )
        task = asyncio.create_task(
            runner.run(state, user_id=user_id, photos=photos, days=days)
        )
        task.add_done_callback(lambda t: t.exception())  # 防 unretrieved-exception 噪音

        return JSONResponse({
            "job_id": state["job_id"],
            "total": len(photos),
            "items": [
                {"filename": it["filename"], "target_date": it["target_date"]}
                for it in state["items"]
            ],
        })

    @router.get("/backfill/status")
    async def backfill_status(job_id: str = "") -> JSONResponse:
        state = BACKFILL_JOBS.get((job_id or "").strip())
        if state is None:
            return JSONResponse(
                {"error": "job 不存在（服务可能已重启，已落库的数据不受影响）"},
                status_code=404,
            )
        return JSONResponse(state)

    @router.get("/profiles")
    async def list_profiles(request: Request, user_id: str = "") -> JSONResponse:
        container = getattr(request.app.state, "container", None)
        if container is None:
            return JSONResponse({"error": "服务尚未完成启动"}, status_code=503)
        user_id = (user_id or "").strip()
        if not user_id:
            return JSONResponse({"error": "缺少 user_id"}, status_code=400)

        from datetime import datetime, timedelta

        rows = await container.skin_profile_repo.list_profiles_in_range(
            user_id,
            datetime.utcnow() - timedelta(days=90),
            datetime.utcnow() + timedelta(days=2),
        )
        out = [
            {
                "profile_id": r.get("profile_id"),
                "created_at": str(r.get("created_at") or ""),
                "overall_state": str(r.get("overall_state") or ""),
                "sync_status": str(r.get("sync_status") or ""),
                "image_url": str(r.get("image_url") or ""),
            }
            for r in rows
        ]
        return JSONResponse({"profiles": out})

    @router.get("/memory")
    async def memory(request: Request, user_id: str = "") -> JSONResponse:
        container = getattr(request.app.state, "container", None)
        if container is None:
            return JSONResponse({"error": "服务尚未完成启动"}, status_code=503)
        user_id = (user_id or "").strip()
        if not user_id:
            return JSONResponse({"error": "缺少 user_id"}, status_code=400)
        try:
            snapshot = await memory_snapshot(container, user_id)
        except Exception as exc:
            logger.exception("lab.memory snapshot failed")
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse(snapshot)

    @router.get("/imagegen/defaults")
    async def imagegen_defaults() -> JSONResponse:
        try:
            return JSONResponse(get_spec_defaults())
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @router.post("/imagegen/generate")
    async def imagegen_generate(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "请求体须为 JSON"}, status_code=400)
        try:
            state = new_imagegen_job(
                days_input=body.get("days") or [],
                model=str(body.get("model") or ""),
                size=str(body.get("size") or "1536x2048"),
                edit_template=str(body.get("edit_template") or ""),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        task = asyncio.create_task(run_generation(state))
        task.add_done_callback(lambda t: t.exception())
        return JSONResponse({
            "job_id": state["job_id"],
            "items": [
                {
                    "day": it["day"],
                    "label": it["label"],
                    "stage": it["stage"],
                    "error": it["error"],
                }
                for it in state["items"]
            ],
        })

    @router.get("/imagegen/status")
    async def imagegen_status(job_id: str = "") -> JSONResponse:
        state = IMAGEGEN_JOBS.get((job_id or "").strip())
        if state is None:
            return JSONResponse({"error": "job 不存在"}, status_code=404)
        return JSONResponse({
            "job_id": state["job_id"],
            "state": state["state"],
            "error": state.get("error", ""),
            "items": [
                {
                    "day": it["day"],
                    "label": it["label"],
                    "stage": it["stage"],
                    "error": it["error"],
                }
                for it in state["items"]
            ],
        })

    @router.get("/imagegen/image")
    async def imagegen_image(job_id: str = "", day: int = 1) -> Response:
        img = get_image((job_id or "").strip(), day)
        if img is None:
            return JSONResponse({"error": "图片不存在"}, status_code=404)
        return Response(content=img, media_type="image/png")

    @router.get("/imagegen/zip")
    async def imagegen_zip(job_id: str = "") -> Response:
        zipped = get_imagegen_zip((job_id or "").strip())
        if zipped is None:
            return JSONResponse({"error": "job 不存在或尚未完成"}, status_code=404)
        return Response(
            content=zipped,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=\"imagegen_{job_id[:8]}.zip\""},
        )

    @router.get("/imagegen/jobs")
    async def imagegen_jobs() -> JSONResponse:
        return JSONResponse({"jobs": list_done_jobs()})

    @router.post("/imagegen/backfill")
    async def imagegen_backfill(request: Request) -> JSONResponse:
        container = getattr(request.app.state, "container", None)
        if container is None:
            return JSONResponse({"error": "服务尚未完成启动，请稍后重试"}, status_code=503)

        body = await request.json()
        job_id = (body.get("job_id") or "").strip()
        user_id = (body.get("user_id") or "").strip()

        if not _USER_ID_RE.match(user_id):
            return JSONResponse(
                {"error": "user_id 只能包含字母/数字/下划线/连字符（1-64 位）"},
                status_code=400,
            )

        photos = get_imagegen_photos(job_id)
        if photos is None:
            return JSONResponse({"error": "imagegen job 不存在或尚未完成"}, status_code=400)

        from Mojing.config import (
            make_image_analysis_url,
            make_lab_backfill_profile_timeout_s,
            make_tos_config,
        )

        uploader = TosUploader(make_tos_config())
        if not uploader.configured:
            return JSONResponse(
                {"error": "TOS 未配置：请在 .env 中填写 TOS_ACCESS_KEY / TOS_SECRET_KEY"},
                status_code=503,
            )

        days = backfill_dates(len(photos))
        bf_state = new_job_state(user_id=user_id, photos=photos, days=days)

        from Mojing.runtime.executors import make_image_analysis_executor

        runner = BackfillRunner(
            uploader=uploader,
            image_repo=container.image_repo,
            skin_profile_repo=container.skin_profile_repo,
            runtime=container.runtime,
            analysis_execute=make_image_analysis_executor(
                make_image_analysis_url(),
                container.image_repo,
            ),
            profile_timeout_s=float(make_lab_backfill_profile_timeout_s()),
        )
        task = asyncio.create_task(
            runner.run(bf_state, user_id=user_id, photos=photos, days=days)
        )
        task.add_done_callback(lambda t: t.exception())

        return JSONResponse({
            "job_id": bf_state["job_id"],
            "total": len(photos),
            "items": [
                {"filename": it["filename"], "target_date": it["target_date"]}
                for it in bf_state["items"]
            ],
        })

    return router
