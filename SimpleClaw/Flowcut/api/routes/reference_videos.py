"""FlowCut 参考视频（爆款视频）管理 REST 接口。"""
from __future__ import annotations

import os
import tempfile
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from Flowcut.api.deps import require_tenant

from Flowcut.api.container import AppContainer
from Flowcut.runtime.streams import FlowcutTaskStream
from Flowcut.storage.oss_client import build_oss_client
from simpleclaw.runtime.task_protocol import TaskEnvelope

router = APIRouter(prefix="/reference-videos", tags=["reference-videos"])


class UploadTokenRequest(BaseModel):
    tenant_key: str | None = None
    filename: str
    product: str | None = None


# ── Routes ─────────────────────────────────────────────────────


@router.post("/upload")
async def upload_reference_video(
    request: Request,
    file: UploadFile = File(...),
    tenant_key: str = Depends(require_tenant),
    product: str | None = Form(None),
    workflow_type: str = Form("reference_video"),
    continuation_type: str = Form("unspecified"),
    connector_ref_video_id: int | None = Form(None),
):
    """上传参考视频。

    workflow_type=pending 时只保存视频记录，等待 Agent 工具决定后续拆镜类型。
    其他 workflow_type 保持一键上传并入队拆镜。
    """
    container: AppContainer = request.app.state.container

    filename = file.filename or "upload"
    oss_key = f"uploads/{tenant_key}/{int(time.time())}_{filename}"

    content = await file.read()
    file_size = len(content)

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(content)

        oss_client = build_oss_client()
        oss_client.upload(tmp_path, oss_key)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    ext = os.path.splitext(filename)[1].lower()
    duration = 0.0  # 拆镜时可通过 FFmpeg 获取

    ref_video = await container.ref_video_repo.create(
        tenant_key=tenant_key,
        oss_key=oss_key,
        oss_url=oss_key,
        name=filename.replace(f".{ext}", "") if ext else filename,
        duration=duration,
        file_size=file_size,
        product=product,
    )

    if workflow_type == "pending":
        return {
            "ref_video_id": ref_video["id"],
            "script_id": None,
            "task_id": None,
            "oss_key": oss_key,
            "product": product,
            "workflow_type": workflow_type,
            "continuation_type": continuation_type,
            "connector_ref_video_id": connector_ref_video_id,
            "status": "pending",
            "message": "视频上传成功，等待选择拆解方式",
        }

    # 同步预建 fc_script(PROCESSING, segments=[]) 并回填 ref_video.script_id，
    # 前端可在 /upload 响应中拿到 script_id 立即跳转到脚本编辑页。
    script = await container.script_repo.create(
        tenant_key=tenant_key,
        source="decomposed",
        segments=[],
        reference_video_id=ref_video["id"],
        product=product,
        status="PROCESSING",
    )
    await container.ref_video_repo.set_script_id(ref_video["id"], script["id"])

    # 自动入队 SCENE_DECOMPOSE 任务
    envelope = TaskEnvelope(
        task_type="scene_decompose",
        payload={
            "ref_video_id": ref_video["id"],
            "oss_key": oss_key,
            "oss_url": oss_key,
            "tenant_key": tenant_key,
            "workflow_type": workflow_type,
            "continuation_type": continuation_type,
            "connector_ref_video_id": connector_ref_video_id,
        },
        stream=FlowcutTaskStream.SCENE_DECOMPOSE,
        tenant_key=tenant_key,
        scope_key=f"scene_decompose:{ref_video['id']}",
    )
    await container.runtime.submit_task(envelope)
    task_id = envelope.task_id

    return {
        "ref_video_id": ref_video["id"],
        "script_id": script["id"],
        "task_id": task_id,
        "oss_key": oss_key,
        "product": product,
        "workflow_type": workflow_type,
        "continuation_type": continuation_type,
        "connector_ref_video_id": connector_ref_video_id,
        "status": "queued",
        "message": "参考视频上传成功，拆镜任务已入队",
    }


@router.post("/upload-token")
async def create_upload_token(
    body: UploadTokenRequest,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """返回 OSS presigned PUT URL 和预分配的 ref_video_id。"""
    container: AppContainer = request.app.state.container

    oss_key = f"uploads/{tenant_key}/{int(time.time())}_{body.filename}"

    ref_video = await container.ref_video_repo.create(
        tenant_key=tenant_key,
        oss_key=oss_key,
        oss_url=oss_key,
        name=body.filename,
        duration=0.0,
        file_size=0,
        product=body.product,
    )

    oss_client = build_oss_client()
    presigned_url = oss_client.presigned_put_url(oss_key)

    return {
        "ref_video_id": ref_video["id"],
        "presigned_url": presigned_url,
        "oss_key": oss_key,
    }


@router.post("/{ref_video_id}/decompose")
async def decompose_reference_video(
    ref_video_id: int,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """手动触发指定参考视频的拆镜任务。"""
    container: AppContainer = request.app.state.container

    ref_video = await container.ref_video_repo.get(ref_video_id)
    if ref_video is None or ref_video.get("tenant_key") != tenant_key:
        raise HTTPException(404, "参考视频不存在")
    if ref_video["status"] != "PROCESSING":
        raise HTTPException(400, f"参考视频状态为 {ref_video['status']}，不能重复拆镜")

    # 复用已预建的 script；若是老数据（无 script_id），按需补建。
    script_id = ref_video.get("script_id")
    if script_id is None:
        script = await container.script_repo.create(
            tenant_key=ref_video["tenant_key"],
            source="decomposed",
            segments=[],
            reference_video_id=ref_video_id,
            product=ref_video.get("product"),
            status="PROCESSING",
        )
        script_id = script["id"]
        await container.ref_video_repo.set_script_id(ref_video_id, script_id)

    envelope = TaskEnvelope(
        task_type="scene_decompose",
        payload={
            "ref_video_id": ref_video_id,
            "oss_key": ref_video["oss_key"],
            "oss_url": ref_video["oss_url"],
            "tenant_key": ref_video["tenant_key"],
        },
        stream=FlowcutTaskStream.SCENE_DECOMPOSE,
        tenant_key=ref_video["tenant_key"],
        scope_key=f"scene_decompose:{ref_video_id}",
    )
    await container.runtime.submit_task(envelope)
    return {
        "ref_video_id": ref_video_id,
        "script_id": script_id,
        "task_id": envelope.task_id,
        "status": "queued",
    }


@router.get("")
async def list_reference_videos(
    request: Request,
    tenant_key: str = Depends(require_tenant),
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """列出租户的参考视频。"""
    container: AppContainer = request.app.state.container
    rows = await container.ref_video_repo.list_by_tenant(
        tenant_key, status=status, limit=limit, offset=offset,
    )
    return rows


@router.get("/{ref_video_id}")
async def get_reference_video(
    ref_video_id: int,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """查询单个参考视频详情。"""
    container: AppContainer = request.app.state.container
    ref_video = await container.ref_video_repo.get(ref_video_id)
    if ref_video is None or ref_video.get("tenant_key") != tenant_key:
        raise HTTPException(404, "参考视频不存在")
    return ref_video
