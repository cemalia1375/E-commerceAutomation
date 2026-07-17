"""成片（fc_creative）路由：列表、详情、标签更新、直接上传。

响应含千川数据回流字段 qc_*。
"""
from __future__ import annotations

import asyncio
import os
import json
import re
import secrets
import shutil
import tempfile
import time
import urllib.parse
import zipfile
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel

from Flowcut.api.deps import require_tenant
from Flowcut.services.highlight_progress import build_highlight_batch_snapshot

from Flowcut.runtime.streams import FlowcutTaskStream
from Flowcut.storage.oss_client import build_oss_client, get_url_cache
from simpleclaw.runtime.task_protocol import TaskEnvelope

router = APIRouter(prefix="/creatives", tags=["creatives"])

_VIDEO_EXTS = {"mp4", "mov", "avi", "mkv", "webm", "flv", "wmv"}
_MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB

# 一次性下载 token（内存），TTL 5 分钟。ZIP 直接从服务器流式输出，不上传 OSS。
_DOWNLOAD_TOKENS: dict[str, dict] = {}
_TOKEN_TTL = 300


def _safe_filename(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)


def _make_download_token(tenant_key: str, rows: list[dict]) -> str:
    now = time.time()
    expired = [k for k, v in list(_DOWNLOAD_TOKENS.items()) if v["expires_at"] < now]
    for k in expired:
        _DOWNLOAD_TOKENS.pop(k, None)
    token = secrets.token_urlsafe(32)
    _DOWNLOAD_TOKENS[token] = {"tenant_key": tenant_key, "rows": rows, "expires_at": now + _TOKEN_TTL}
    return token


def _pop_download_token(token: str) -> dict | None:
    entry = _DOWNLOAD_TOKENS.pop(token, None)
    if entry is None or time.time() > entry["expires_at"]:
        return None
    return entry


class BatchDownloadZipRequest(BaseModel):
    tenant_key: str | None = None
    creative_ids: list[int]


class BatchDownloadZipByKeysRequest(BaseModel):
    items: list[dict]  # [{oss_key: str, filename: str}]


def _serialize_creative(row: dict[str, Any]) -> dict[str, Any]:
    """把 db row 序列化为 API 响应 dict，含 qc_* 字段。datetime 转 ISO 字符串。"""
    result = dict(row)
    oss = build_oss_client()
    cache = get_url_cache()
    # 时间字段统一转 ISO 8601 字符串
    for key in ("created_at", "updated_at", "qc_synced_at"):
        v = result.get(key)
        if isinstance(v, datetime):
            # DB 存 UTC naive datetime；标记为 UTC 使 isoformat 输出 +00:00，浏览器才能正确转为本地时间
            result[key] = v.replace(tzinfo=timezone.utc).isoformat()
        elif v is not None:
            result[key] = str(v)
    # 保证 qc_* 字段存在（旧行可能没有这些列）
    for field in ("qc_material_id", "qc_cost", "qc_impressions",
                  "qc_clicks", "qc_conversions", "qc_synced_at"):
        result.setdefault(field, None)
    for field in ("highlight_reason_json", "compose_plan_json"):
        value = result.get(field)
        if isinstance(value, str) and value:
            try:
                result[field] = json.loads(value)
            except json.JSONDecodeError:
                pass
    oss_url = result.get("oss_url")
    if isinstance(oss_url, str) and oss_url and not oss_url.startswith(("http://", "https://")):
        result["oss_url"] = cache.get_or_set(oss_url, lambda k=oss_url: oss.presigned_get_url(k))
    thumbnail_url = result.get("thumbnail_url")
    if isinstance(thumbnail_url, str) and thumbnail_url and not thumbnail_url.startswith(("http://", "https://")):
        result["thumbnail_url"] = cache.get_or_set(thumbnail_url, lambda k=thumbnail_url: oss.presigned_get_url(k))
    for asset_url_field in ("source_asset_oss_url", "connector_asset_oss_url"):
        asset_url = result.get(asset_url_field)
        if (
            isinstance(asset_url, str)
            and asset_url
            and not asset_url.startswith(("http://", "https://"))
        ):
            result[asset_url_field] = cache.get_or_set(asset_url, lambda k=asset_url: oss.presigned_get_url(k))
    return result


def _ext_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _sanitize_path_component(value: str) -> str:
    import re
    return re.sub(r"[/\\]|\.{2,}", "_", value)


def _make_creative_oss_key(tenant_key: str, filename: str) -> str:
    safe_tenant = _sanitize_path_component(tenant_key)
    safe_filename = _sanitize_path_component(filename)
    return f"creatives/{safe_tenant}/uploads/{int(time.time())}_{safe_filename}"


@router.post("/upload")
async def upload_creative(
    request: Request,
    file: UploadFile = File(...),
    tenant_key: str = Depends(require_tenant),
):
    """直接上传成片视频，落 fc_creative(status=READY, label=NORMAL)。

    用于「我手里已经有成片」的快捷入库路径，不走 compose_video 拼片流程，
    也不绑定 script_id / session_key。
    """
    container = request.app.state.container

    filename = file.filename or "creative.mp4"
    ext = _ext_of(filename)
    if ext not in _VIDEO_EXTS:
        raise HTTPException(415, f"不支持的成片格式 .{ext or '(无扩展名)'}；仅支持视频")

    content = await file.read()
    if len(content) > _MAX_UPLOAD_SIZE:
        raise HTTPException(413, "成片超过 500 MB 限制")

    oss_key = _make_creative_oss_key(tenant_key, filename)

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(content)
        try:
            build_oss_client().upload(tmp_path, oss_key)
        except Exception as exc:
            raise HTTPException(500, f"OSS 上传失败: {exc}") from exc
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # 用 session_key='manual_upload' 标记非对话来源；script_id 留 NULL
    creative = await container.creative_repo.create(
        tenant_key=tenant_key,
        session_key="manual_upload",
        script_id=None,
    )
    await container.creative_repo.update_status(
        creative["id"], "READY",
        oss_key=oss_key,
        oss_url=oss_key,
    )

    row = await container.creative_repo.get(creative["id"])
    return {"ok": True, "data": _serialize_creative(row)}


@router.post("/batch-download-zip/prepare")
async def batch_download_zip_prepare(
    body: BatchDownloadZipRequest,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """校验成片 ID，生成一次性下载 token（5 分钟有效，单次使用）。

    ZIP 直接从服务端流式输出，不上传 OSS，不产生存储堆积。
    """
    if not body.creative_ids:
        raise HTTPException(400, "creative_ids 不能为空")
    if len(body.creative_ids) > 50:
        raise HTTPException(400, "单次最多打包 50 个成片")

    container = request.app.state.container
    rows = []
    for cid in body.creative_ids:
        row = await container.creative_repo.get(cid)
        if row and row.get("tenant_key") == tenant_key and row.get("oss_key"):
            rows.append({
                "id": row["id"],
                "oss_key": str(row["oss_key"]),
                "source_drama_name": row.get("source_drama_name"),
                "source_asset_name": row.get("source_asset_name"),
                "source_episode_no": row.get("source_episode_no"),
            })

    if not rows:
        raise HTTPException(422, "没有可下载的已合成成片（请确认成片已生成）")

    token = _make_download_token(tenant_key, rows)
    return {"ok": True, "token": token, "count": len(rows)}


@router.post("/batch-download-zip/prepare-by-keys")
async def batch_download_zip_by_keys_prepare(
    body: BatchDownloadZipByKeysRequest,
    tenant_key: str = Depends(require_tenant),
):
    """接收明确的 oss_key 列表打包，供批量导出（含数字人/前贴合成产物）使用。"""
    if not body.items:
        raise HTTPException(400, "items 不能为空")
    if len(body.items) > 50:
        raise HTTPException(400, "单次最多打包 50 个成片")
    rows = [
        {
            "oss_key": str(item["oss_key"]),
            "filename": _safe_filename(str(item.get("filename") or f"highlight_{i + 1}.mp4")),
        }
        for i, item in enumerate(body.items)
        if item.get("oss_key")
    ]
    if not rows:
        raise HTTPException(422, "没有有效的 oss_key")
    token = _make_download_token(tenant_key, rows)
    return {"ok": True, "token": token, "count": len(rows)}


@router.get("/batch-download-zip/{token}")
async def batch_download_zip_stream(token: str):
    """用一次性 token 触发流式 ZIP 下载。ZIP 在服务端临时目录组装后直接流出，完成即删。"""
    entry = _pop_download_token(token)
    if entry is None:
        raise HTTPException(404, "下载链接已失效或已使用，请重新点击「批量导出」")

    rows = entry["rows"]

    ts = int(time.time())
    drama_names = list({str(r.get("source_drama_name") or "高光") for r in rows})[:3]
    zip_label = _safe_filename("_".join(drama_names) or "高光合集")
    zip_filename = f"{zip_label}_{ts}.zip"

    def _build_zip() -> tuple[str, str]:
        tmpdir = tempfile.mkdtemp()
        zip_path = os.path.join(tmpdir, "highlights.zip")
        oss = build_oss_client()
        packed = 0
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
            for i, row in enumerate(rows):
                oss_key = row["oss_key"]
                if row.get("filename"):
                    arcname = row["filename"]
                else:
                    cid = row["id"]
                    drama = str(row.get("source_drama_name") or row.get("source_asset_name") or "高光")
                    episode = row.get("source_episode_no")
                    arcname = _safe_filename(
                        f"{drama}_第{episode}集_{cid}.mp4" if episode else f"{drama}_{cid}.mp4"
                    )
                video_path = os.path.join(tmpdir, f"{i}.mp4")
                try:
                    oss.download(oss_key, video_path)
                except Exception:
                    continue
                zf.write(video_path, arcname)
                packed += 1

        if packed == 0:
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise RuntimeError("所有成片 OSS 下载均失败，无法打包")
        return zip_path, tmpdir

    try:
        zip_path, tmpdir = await asyncio.get_event_loop().run_in_executor(None, _build_zip)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc

    content_length = os.path.getsize(zip_path)
    encoded_name = urllib.parse.quote(zip_filename)

    async def iter_zip():
        try:
            with open(zip_path, "rb") as f:
                while chunk := f.read(65536):
                    yield chunk
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    return StreamingResponse(
        iter_zip(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}",
            "Content-Length": str(content_length),
        },
    )


@router.get("")
async def list_creatives(
    request: Request,
    tenant_key: str = Depends(require_tenant),
    limit: int = 50,
    offset: int = 0,
):
    limit = max(1, min(limit, 500))
    c = request.app.state.container
    rows = await c.creative_repo.list_by_tenant(
        tenant_key,
        limit=limit,
        offset=offset,
    )
    return {"ok": True, "data": [_serialize_creative(r) for r in rows]}


@router.get("/highlight-plan-tasks")
async def list_highlight_plan_tasks(
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """列出本租户在途（queued/running）的跨集高光规划任务，供成片库展示"生成中"占位。

    规划任务跑完才会一次性建 fc_creative 行，这之前库里没有任何记录；前端据此渲染
    占位卡，避免静默等待。同时涵盖 highlight_plan（旧管道）和 highlight_batch（新管道）。
    """
    c = request.app.state.container
    tasks = await c.task_repo.list_active(
        tenant_key=tenant_key, task_types=("highlight_plan",),
    )
    data = [
        {
            "task_id": t.get("task_id"),
            "status": t.get("status"),
            # batch 管道 payload.drama_name 为单数，旧管道 payload.drama_names 为复数列表
            "drama_name": (
                ((t.get("payload") or {}).get("drama_names") or [None])[0]
                or (t.get("payload") or {}).get("drama_name")
            ),
            "num_candidates": (t.get("payload") or {}).get("num_candidates"),
            "batch_id": (t.get("payload") or {}).get("batch_id"),
            "created_at": t.get("created_at"),
        }
        for t in tasks
    ]
    active_batches = await c.highlight_batch_repo.list_active(tenant_key)
    known_batch_ids = {item.get("batch_id") for item in data}
    for batch in active_batches:
        batch_id = str(batch.get("batch_id") or "")
        if not batch_id or batch_id in known_batch_ids:
            continue
        snapshot = await build_highlight_batch_snapshot(
            c.highlight_batch_repo, batch,
        )
        data.append({
            "task_id": snapshot["task_id"],
            "status": snapshot["status"],
            "drama_name": batch.get("drama_name"),
            "num_candidates": batch.get("num_candidates"),
            "batch_id": batch_id,
            "stage": batch.get("status"),
            "progress": snapshot["progress"],
            "created_at": batch.get("created_at"),
        })
    return {"ok": True, "data": data}


@router.get("/highlight-by-script/{script_id}")
async def get_highlight_creative_by_script(
    script_id: int,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    c = request.app.state.container
    row = await c.creative_repo.find_latest_highlight_by_script(
        tenant_key=tenant_key,
        script_id=script_id,
    )
    return {"ok": True, "data": _serialize_creative(row) if row else None}


@router.get("/{creative_id}")
async def get_creative(
    creative_id: int,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    c = request.app.state.container
    row = await c.creative_repo.get(creative_id)
    if row is None or row.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"creative {creative_id} not found")
    return {"ok": True, "data": _serialize_creative(row)}


class LabelUpdate(BaseModel):
    label: str  # NORMAL / HOT / DEAD


@router.patch("/{creative_id}/label")
async def update_label(
    creative_id: int,
    body: LabelUpdate,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    allowed = {"NORMAL", "HOT", "DEAD"}
    if body.label not in allowed:
        raise HTTPException(400, f"label 必须是 {allowed} 之一")
    c = request.app.state.container
    row = await c.creative_repo.get(creative_id)
    if row is None or row.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"creative {creative_id} not found")
    await c.creative_repo.update_label(creative_id, body.label)
    return {"ok": True}


@router.delete("/{creative_id}")
async def delete_creative(
    creative_id: int,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """删除成片：清 fc_material_usage 关联 + fc_creative 行 + OSS 视频/字幕文件。

    保留源高光资产（source_asset_id / connector_asset_id 指向的 fc_highlight_asset），
    它们可被其它成片复用，不随成片删除而清除。
    """
    c = request.app.state.container
    row = await c.creative_repo.get(creative_id)
    if row is None or row.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"creative {creative_id} not found")

    # 删除 OSS 上的成片视频与字幕；只删存为 OSS key 的字段（跳过 http(s) 外链）
    for key in (row.get("oss_key"), row.get("srt_url")):
        if key and not str(key).startswith(("http://", "https://")):
            try:
                c.oss_client.delete_object(str(key))
            except Exception:
                pass

    await c.creative_repo.delete(creative_id)
    return {"ok": True, "deleted": 1}


@router.post("/{creative_id}/compose-highlight")
async def compose_highlight_creative(
    creative_id: int,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    c = request.app.state.container
    row = await c.creative_repo.get(creative_id)
    if row is None or row.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"creative {creative_id} not found")
    creative_type = str(row.get("creative_type") or "")
    if creative_type not in {"highlight_original", "highlight_digital_human",
                             "continuous_cross_episode"}:
        raise HTTPException(422, "只有高光成片记录可以触发高光合成")
    if creative_type == "continuous_cross_episode":
        # 跨集高光靠 clip_plan_json 逐集裁切，没有 highlight_end
        if not row.get("clip_plan_json"):
            raise HTTPException(422, "该跨集高光记录没有切片计划，无法合成")
    elif row.get("highlight_start") is None or row.get("highlight_end") is None:
        raise HTTPException(422, "该高光记录还没有高光区间，无法合成")

    await c.creative_repo.update_status(creative_id, "PROCESSING")
    envelope = TaskEnvelope(
        task_type="highlight_compose",
        payload={"creative_id": creative_id},
        stream=FlowcutTaskStream.VIDEO_COMPOSE,
        tenant_key=tenant_key,
        session_key=str(row.get("session_key") or "") or None,
        scope_key=f"highlight_compose:{creative_id}",
    )
    queue_id = await c.runtime.submit_task(
        envelope,
        tool_name="highlight_compose",
        summary=f"compose highlight creative {creative_id}",
    )
    return {
        "ok": True,
        "task_id": envelope.task_id,
        "queue_id": queue_id,
        "creative_id": creative_id,
        "status": "queued",
    }


class ConnectorUpdate(BaseModel):
    # 跨集高光要拼接的数字人连接器资产 id；null = 清空（纯片）。
    connector_asset_id: int | None = None


@router.patch("/{creative_id}/connector")
async def set_creative_connector(
    creative_id: int,
    body: ConnectorUpdate,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """持久化跨集高光要拼接的数字人选择（仅记录，不合成；拼接发生在导出时）。"""
    c = request.app.state.container
    row = await c.creative_repo.get(creative_id)
    if row is None or row.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"creative {creative_id} not found")
    connector_id = body.connector_asset_id
    if connector_id is not None:
        connector = await c.highlight_asset_repo.get(int(connector_id))
        if (connector is None or connector.get("tenant_key") != tenant_key
                or connector.get("asset_type") != "digital_human_connector"):
            raise HTTPException(422, "connector_asset_id 不是有效的数字人素材")
    await c.creative_repo.set_connector_asset(creative_id, connector_id)
    return {"ok": True, "creative_id": creative_id, "connector_asset_id": connector_id}


class PrerollUpdate(BaseModel):
    # 高光成片片头贴片资产 id；null = 清空。
    preroll_asset_id: int | None = None


@router.patch("/{creative_id}/preroll")
async def set_creative_preroll(
    creative_id: int,
    body: PrerollUpdate,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """持久化高光成片要叠加的片头贴片选择（仅记录，不合成；拼接发生在导出时）。"""
    c = request.app.state.container
    row = await c.creative_repo.get(creative_id)
    if row is None or row.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"creative {creative_id} not found")
    preroll_id = body.preroll_asset_id
    if preroll_id is not None:
        preroll = await c.highlight_asset_repo.get(int(preroll_id))
        if (preroll is None or preroll.get("tenant_key") != tenant_key
                or preroll.get("asset_type") != "preroll"):
            raise HTTPException(422, "preroll_asset_id 不是有效的片头贴片素材")
    await c.creative_repo.set_preroll_asset(creative_id, preroll_id)
    return {"ok": True, "creative_id": creative_id, "preroll_asset_id": preroll_id}


@router.get("/{creative_id}/download")
async def download_creative(
    creative_id: int,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """纯片下载：302 跳转到带 attachment 文件名的 presigned URL。

    浏览器顶层导航打开此接口即按文件名下载，绕开 OSS CORS（不走 fetch）。
    选了数字人的拼接导出走 /export-highlight 异步任务，不经此接口。
    """
    c = request.app.state.container
    row = await c.creative_repo.get(creative_id)
    if row is None or row.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"creative {creative_id} not found")
    oss_key = row.get("oss_key")
    if not oss_key:
        raise HTTPException(422, "该成片还没有可下载的视频")
    if row.get("connector_asset_id") is not None or row.get("preroll_asset_id") is not None:
        raise HTTPException(
            422,
            "该成片已选择数字人或片头贴片，请通过「导出」接口生成拼接成品后再下载",
        )
    drama = str(row.get("source_drama_name") or row.get("source_asset_name") or "高光")
    url = build_oss_client().presigned_get_url(
        str(oss_key), disposition_filename=f"{drama}_{creative_id}.mp4")
    if not url:
        raise HTTPException(503, "OSS 未配置，无法下载")
    return RedirectResponse(url, status_code=307)


@router.post("/{creative_id}/export-highlight")
async def export_highlight_creative(
    creative_id: int,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """导出跨集高光：把已合成的 1 分钟片与所选数字人用 ffmpeg 拼接，产出可下载 mp4。

    异步任务（VIDEO_COMPOSE 流，task_type=highlight_export），前端轮询 /tasks/{id}
    取 result_url 下载。没选数字人时前端应直接下载纯片，不必走此接口。
    """
    c = request.app.state.container
    row = await c.creative_repo.get(creative_id)
    if row is None or row.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"creative {creative_id} not found")
    if not row.get("oss_key"):
        raise HTTPException(422, "该成片还没有合成出 1 分钟片，无法导出")
    if row.get("connector_asset_id") is None and row.get("preroll_asset_id") is None:
        raise HTTPException(422, "未选择数字人或片头贴片；纯片请直接下载，无需拼接导出")

    envelope = TaskEnvelope(
        task_type="highlight_export",
        payload={"creative_id": creative_id},
        stream=FlowcutTaskStream.VIDEO_COMPOSE,
        tenant_key=tenant_key,
        session_key=str(row.get("session_key") or "") or None,
        scope_key=f"highlight_export:{creative_id}",
    )
    queue_id = await c.runtime.submit_task(
        envelope,
        tool_name="highlight_export",
        summary=f"export highlight creative {creative_id}",
    )
    return {
        "ok": True,
        "task_id": envelope.task_id,
        "queue_id": queue_id,
        "creative_id": creative_id,
        "status": "queued",
    }
