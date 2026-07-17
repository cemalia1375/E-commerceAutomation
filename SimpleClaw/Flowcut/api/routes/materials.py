"""FlowCut 素材管理 REST 接口。"""

import io
import logging
import os
import shutil
import tempfile
import time
import uuid
import zipfile as _zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict

from Flowcut.api.deps import require_tenant

logger = logging.getLogger(__name__)

# PATCH /materials/{id} 允许修改的字段白名单
_MATERIAL_PATCH_ALLOWED_FIELDS = frozenset({"name", "product", "scene_role"})

from Flowcut.api.container import AppContainer
from Flowcut.runtime.streams import FlowcutTaskStream
from Flowcut.services.douyin_client import DouyinClient
from Flowcut.services.material_matcher import match_segments_parallel
from Flowcut.storage.oss_client import build_oss_client, get_url_cache
from simpleclaw.runtime.task_protocol import TaskEnvelope

router = APIRouter(prefix="/materials", tags=["materials"])


# ── Pydantic models ────────────────────────────────────────────


class UploadTokenRequest(BaseModel):
    tenant_key: str | None = None
    filename: str
    product: str
    scene_role: str | None = None


class UpdateMaterialRequest(BaseModel):
    """素材 PATCH 请求白名单：仅允许修改 name / product / scene_role。

    其它字段（oss_key / status / transcript / category / description 等）一律由后端管控，
    传入会被 Pydantic 校验为 422。
    """
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    product: str | None = None
    scene_role: str | None = None


class ImportDouyinRequest(BaseModel):
    share_url: str
    tenant_key: str | None = None
    product: str
    scene_role: str | None = None


class MatchSegmentRequest(BaseModel):
    index: int
    description: str
    duration: float = 0.0


class MatchMaterialsRequest(BaseModel):
    tenant_key: str | None = None
    product: str
    segments: list[MatchSegmentRequest]


# ── Helpers ────────────────────────────────────────────────────


def _sanitize_path_component(value: str) -> str:
    """Replace path separators and parent-dir sequences with underscores."""
    import re
    return re.sub(r"[/\\]|\.{2,}", "_", value)


def _make_upload_oss_key(
    tenant_key: str, product: str | None, filename: str, *, ts: int | None = None
) -> str:
    """生成上传素材的 OSS key，按产品分层。"""
    product_dir = product if product else "通用"
    timestamp = ts if ts is not None else int(time.time())
    safe_tenant = _sanitize_path_component(tenant_key)
    safe_product = _sanitize_path_component(product_dir)
    safe_filename = _sanitize_path_component(filename)
    return f"materials/{safe_tenant}/{safe_product}/uploads/{timestamp}_{safe_filename}"


_VIDEO_EXTS = {"mp4", "mov", "avi", "mkv", "webm", "flv", "wmv"}
_AUDIO_EXTS = {"mp3", "wav", "aac", "ogg", "wma", "flac", "m4a"}
_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "svg"}
_ALLOWED_SINGLE_UPLOAD_EXTS = _VIDEO_EXTS | _AUDIO_EXTS | _IMAGE_EXTS


def _ext_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _category_from_filename(filename: str) -> str:
    ext = _ext_of(filename)
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _AUDIO_EXTS:
        return "audio"
    if ext in _IMAGE_EXTS:
        return "image"
    return "video"


def _reject_if_not_single_media(filename: str) -> None:
    """单文件上传只接受视频 / 音频 / 图片；其它后缀（特别是 .zip）拒掉。"""
    ext = _ext_of(filename)
    if ext == "zip":
        raise HTTPException(
            415,
            "压缩包请使用 /materials/upload-zip 接口上传，不要走单文件入口",
        )
    if ext not in _ALLOWED_SINGLE_UPLOAD_EXTS:
        raise HTTPException(
            415,
            f"不支持的文件类型 .{ext or '(无扩展名)'}；仅支持视频 / 音频 / 图片",
        )


def _resolve_material_urls(material: dict) -> dict:
    """将物料中的 OSS key 实时转为预签名 URL（适配私有 bucket）。

    thumbnail_url / preview_url / oss_url 存储的是 OSS key 而非公开 URL，
    客户端需要预签名 URL 才能访问。使用进程内缓存复用已生成的 URL，
    避免每次请求产生不同的签名字符串导致前端 <video>/<img> 重载。
    """
    oss = build_oss_client()
    cache = get_url_cache()
    result = dict(material)

    for field in ("thumbnail_url", "preview_url", "oss_url"):
        val = result.get(field)
        if val and not val.startswith("http"):
            result[field] = cache.get_or_set(val, lambda v=val: oss.presigned_get_url(v))

    return result


# ── Routes ─────────────────────────────────────────────────────


@router.post("/upload")
async def upload_material(
    request: Request,
    file: UploadFile = File(...),
    tenant_key: str = Depends(require_tenant),
    product: str = Form(...),
    scene_role: str | None = Form(None),
):
    """接收浏览器上传的文件，服务端直传 TOS，返回 material_id。

    替代 upload-token + 浏览器直传方案，避免 TOS bucket CORS 配置问题。
    """
    container: AppContainer = request.app.state.container

    filename = file.filename or "upload"
    _reject_if_not_single_media(filename)
    oss_key = _make_upload_oss_key(tenant_key, product or None, filename)
    category = _category_from_filename(filename)

    # Read file into a temp file so we know the real size before creating the DB record
    content = await file.read()
    if len(content) > _MAX_UPLOAD_SIZE:
        raise HTTPException(413, "文件超过 1024 MB 限制")
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

    material = await container.material_repo.create(
        tenant_key=tenant_key,
        oss_key=oss_key,
        oss_url=oss_key,
        name=filename,
        category=category,
        duration=0.0,
        file_size=file_size,
        product=product or None,
        scene_role=scene_role or None,
    )
    material_id = material["id"]

    # 自动入队 MATERIAL_PROCESS 任务（ASR + 封面提取）
    envelope = TaskEnvelope(
        task_type="material_process",
        payload={
            "material_id": material_id,
            "oss_key": oss_key,
            "oss_url": oss_key,
        },
        stream=FlowcutTaskStream.MATERIAL_PROCESS,
        tenant_key=tenant_key,
        scope_key=f"material:{material_id}",
    )
    await container.runtime.submit_task(envelope)
    task_id = envelope.task_id

    return {"material_id": material_id, "task_id": task_id, "oss_key": oss_key, "status": "queued"}


@router.post("/upload-token")
async def create_upload_token(
    body: UploadTokenRequest,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """返回 OSS presigned PUT URL 和预分配的 material_id。"""
    container: AppContainer = request.app.state.container

    _reject_if_not_single_media(body.filename)
    oss_key = _make_upload_oss_key(tenant_key, body.product or None, body.filename)
    category = _category_from_filename(body.filename)

    material = await container.material_repo.create(
        tenant_key=tenant_key,
        oss_key=oss_key,
        oss_url=oss_key,  # 暂时复用 oss_key，后续由 worker 替换为真实 URL
        name=body.filename,
        category=category,
        duration=0.0,
        file_size=0,
        product=body.product or None,
        scene_role=body.scene_role or None,
    )

    oss_client = build_oss_client()
    presigned_url = oss_client.presigned_put_url(oss_key)

    return {
        "material_id": material["id"],
        "presigned_url": presigned_url,
        "oss_key": oss_key,
    }


@router.post("/{material_id}/process")
async def process_material(
    material_id: int,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """前端直传 OSS 完成后调用，触发 MATERIAL_PROCESS 任务入队。"""
    container: AppContainer = request.app.state.container

    material = await container.material_repo.get(material_id)
    if material is None or material.get("tenant_key") != tenant_key:
        raise HTTPException(404, "素材不存在")
    if material["status"] != "PROCESSING":
        raise HTTPException(400, "素材状态不是 PROCESSING，无法提交处理任务")

    envelope = TaskEnvelope(
        task_type="material_process",
        payload={
            "material_id": material_id,
            "oss_key": material["oss_key"],
            "oss_url": material["oss_url"],
        },
        stream=FlowcutTaskStream.MATERIAL_PROCESS,
        tenant_key=material["tenant_key"],
        scope_key=f"material:{material_id}",
    )

    await container.runtime.submit_task(envelope)
    task_id = envelope.task_id

    return {
        "material_id": material_id,
        "task_id": task_id,
        "status": "queued",
    }


@router.post("/import-douyin")
async def import_douyin(
    body: ImportDouyinRequest,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """从抖音分享短链导入视频素材。

    1. 解析 v.douyin.com 短链 → 跟随重定向获取真实 URL
    2. 提取 aweme_id，调用抖音公开 API 获取视频详情
    3. 下载无水印视频 → 上传 OSS → 创建 material 记录
    4. 入队 MATERIAL_PROCESS 任务（ASR + 封面提取）
    """
    container: AppContainer = request.app.state.container

    client = DouyinClient()
    real_url = await client.resolve_short_link(body.share_url)
    aweme_id = client.extract_aweme_id(real_url)
    info = await client.get_video_info(aweme_id)

    # 下载视频到临时文件
    video_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            video_path = tmp.name
        await client.download_video(info.play_url, video_path)

        # 上传到 OSS
        file_size = os.path.getsize(video_path)
        if file_size > _MAX_UPLOAD_SIZE:
            raise HTTPException(413, "抖音视频超过 1024 MB 限制")
        oss_key = _make_upload_oss_key(tenant_key, body.product or None, f"{aweme_id}.mp4")
        oss_client = build_oss_client()
        oss_client.upload(video_path, oss_key)
    finally:
        if video_path and os.path.exists(video_path):
            try:
                os.unlink(video_path)
            except OSError:
                pass

    # 创建素材记录
    duration_s = info.duration_ms / 1000.0
    material = await container.material_repo.create(
        tenant_key=tenant_key,
        oss_key=oss_key,
        oss_url=oss_client.get_public_url(oss_key) or oss_key,
        name=info.title or f"douyin_{aweme_id}",
        category="video",
        duration=duration_s,
        file_size=file_size,
        product=body.product or None,
        scene_role=body.scene_role or None,
    )

    # 入队 MATERIAL_PROCESS 任务
    envelope = TaskEnvelope(
        task_type="material_process",
        payload={
            "material_id": material["id"],
            "oss_key": oss_key,
            "oss_url": oss_key,
        },
        stream=FlowcutTaskStream.MATERIAL_PROCESS,
        tenant_key=tenant_key,
        scope_key=f"material:{material['id']}",
    )
    await container.runtime.submit_task(envelope)
    task_id = envelope.task_id

    return {
        "material_id": material["id"],
        "task_id": task_id,
        "aweme_id": aweme_id,
        "title": info.title,
        "status": "queued",
    }


@router.post("/match")
async def match_materials(
    body: MatchMaterialsRequest,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """按脚本段批量搜索素材库，返回每段双阶段候选（产品专属 + 通用兜底）。

    无状态接口：不依赖脚本持久化，前端可直接传 segments。
    """
    if not body.segments:
        raise HTTPException(400, "segments 不能为空")

    container: AppContainer = request.app.state.container
    oss_client = build_oss_client()
    results = await match_segments_parallel(
        [s.model_dump() for s in body.segments],
        tenant_key=tenant_key,
        product=body.product,
        embedding_service=container.embedding_service,
        vector_store=container.vector_store,
        material_repo=container.material_repo,
        oss_client=oss_client,
    )
    return {"segments": results}


@router.get("/products")
async def list_products(
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """返回该租户既有产品名列表，供前端 AutoComplete 使用。"""
    container: AppContainer = request.app.state.container
    products = await container.material_repo.list_distinct_products(tenant_key)
    return {"products": products, "tenant_key": tenant_key}


def _build_material_tree(rows: list[dict]) -> list[dict]:
    """按 product → scene_role 两级聚合素材行数。"""
    summary: dict[str, dict[str, int]] = {}
    for mat in rows:
        p = mat.get("product") or "通用"
        r = mat.get("scene_role") or "未分类"
        if p not in summary:
            summary[p] = {}
        summary[p][r] = summary[p].get(r, 0) + 1

    tree: list[dict] = []
    for product_name in sorted(summary.keys()):
        roles = summary[product_name]
        total = sum(roles.values())
        children = [
            {"scene_role": role, "count": cnt}
            for role, cnt in sorted(roles.items())
        ]
        tree.append({
            "product": product_name,
            "total_count": total,
            "children": children,
        })
    return tree


@router.get("/tree")
async def material_tree(
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """返回素材库产品→场景角色两级树（结构化字段）。

    包含 PROCESSING + READY 的素材，排除 FAILED，
    确保用户上传后能立即在侧边栏看到进行中的素材。
    """
    container: AppContainer = request.app.state.container
    rows = await container.material_repo.list_by_tenant(
        tenant_key, limit=99999,
    )
    visible_rows = [r for r in rows if r.get("status") in ("PROCESSING", "READY")]
    return _build_material_tree(visible_rows)


@router.get("")
async def list_materials(
    request: Request,
    tenant_key: str = Depends(require_tenant),
    category: str | None = None,
    status: str | None = None,
    product: str | None = None,
    scene_role: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """列出租户的素材，支持 category / status / product / scene_role 过滤。"""
    container: AppContainer = request.app.state.container
    rows = await container.material_repo.list_by_tenant(
        tenant_key,
        category=category,
        status=status,
        product=product,
        scene_role=scene_role,
        limit=limit,
        offset=offset,
    )
    return [_resolve_material_urls(r) for r in rows]


@router.delete("")
async def delete_materials_by_product(
    request: Request,
    product: str,
    tenant_key: str = Depends(require_tenant),
):
    """按产品批量删除素材：Qdrant 向量 → OSS 对象 → MySQL 行。

    与单条 DELETE /materials/{id} 保持一致的容错策略：
    - Qdrant 删除失败 → 跳过该条（fail-fast 不影响其它），记 error
    - OSS 删除失败 → 记 warn 继续
    - MySQL 行最后删
    """
    if not tenant_key or not product:
        raise HTTPException(422, "tenant_key 与 product 均为必填")

    container: AppContainer = request.app.state.container
    rows = await container.material_repo.list_by_tenant(
        tenant_key, product=product, limit=99999,
    )
    if not rows:
        return {"ok": True, "deleted": 0, "errors": []}

    deleted = 0
    errors: list[str] = []
    for mat in rows:
        mid = mat["id"]
        try:
            await container.vector_store.delete(mid)
        except Exception as exc:
            logger.exception("删 Qdrant 向量失败 material_id=%s", mid)
            errors.append(f"material {mid} vector delete failed: {exc}")
            continue
        oss_key = mat.get("oss_key")
        if oss_key:
            try:
                container.oss_client.delete_object(oss_key)
            except Exception as exc:
                logger.warning(
                    "删 OSS 对象失败 material_id=%s key=%s err=%s",
                    mid, oss_key, exc,
                )
        await container.material_repo.delete(mid)
        deleted += 1

    return {"ok": True, "deleted": deleted, "errors": errors}


@router.get("/{material_id}")
async def get_material(
    material_id: int,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """查询单个素材详情。"""
    container: AppContainer = request.app.state.container
    material = await container.material_repo.get(material_id)
    if material is None or material.get("tenant_key") != tenant_key:
        raise HTTPException(404, "素材不存在")
    return _resolve_material_urls(material)


@router.patch("/{material_id}")
async def update_material(
    material_id: int,
    body: UpdateMaterialRequest,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """更新素材的可编辑字段：name / product / scene_role。

    其它字段通过 Pydantic 白名单（extra="forbid"）拒绝，返回 422。
    """
    container: AppContainer = request.app.state.container

    material = await container.material_repo.get(material_id)
    if material is None or material.get("tenant_key") != tenant_key:
        raise HTTPException(404, "素材不存在")

    await container.material_repo.update(
        material_id,
        name=body.name,
        product=body.product,
        scene_role=body.scene_role,
    )
    return await container.material_repo.get(material_id)


@router.delete("/{material_id}")
async def delete_material(
    material_id: int,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """物理删除素材：Qdrant 向量 → OSS 对象 → MySQL 行。

    顺序与容错策略：
    1. 先删 Qdrant 向量。失败 → 抛 500，DB 不动，用户可重试（fail-fast）。
    2. 删 OSS 对象。失败 → 记 warn 继续（OSS 残留比 DB 数据丢失代价小）。
    3. 最后删 MySQL 行。
    """
    container: AppContainer = request.app.state.container

    material = await container.material_repo.get(material_id)
    if material is None or material.get("tenant_key") != tenant_key:
        raise HTTPException(404, "素材不存在")

    # 1) 先删 Qdrant 向量 — 失败则抛 500，保留 DB/OSS 让用户重试
    try:
        await container.vector_store.delete(material_id)
    except Exception as exc:
        logger.exception("删 Qdrant 向量失败 material_id=%s", material_id)
        raise HTTPException(500, f"删除向量失败: {exc}") from exc

    # 2) 删 OSS 对象（主文件） — 失败仅 warn，不阻塞删除
    oss_key = material.get("oss_key")
    if oss_key:
        try:
            container.oss_client.delete_object(oss_key)
        except Exception as exc:
            logger.warning(
                "删 OSS 对象失败 material_id=%s key=%s err=%s",
                material_id, oss_key, exc,
            )

    # 3) 最后删 MySQL 行
    await container.material_repo.delete(material_id)
    return {"ok": True}


_ZIP_UPLOAD_TTL_S = 30 * 60  # 30 分钟
_MAX_ZIP_SIZE = 1024 * 1024 * 1024  # 1024 MB
_MAX_UPLOAD_SIZE = 1024 * 1024 * 1024  # 1024 MB


def _cleanup_expired_zip_uploads(container: AppContainer) -> None:
    """清理过期的 zip 上传临时文件。"""
    now = time.time()
    expired = [
        uid for uid, data in container.zip_uploads.items()
        if now - data["created_at"] > _ZIP_UPLOAD_TTL_S
    ]
    for uid in expired:
        data = container.zip_uploads.pop(uid)
        try:
            os.unlink(data["zip_path"])
        except OSError:
            pass


@router.post("/upload-zip")
async def upload_zip(
    request: Request,
    file: UploadFile = File(...),
    tenant_key: str = Depends(require_tenant),
):
    """上传 zip 文件，解析目录结构返回预览；解压推迟到 confirm 步骤执行。"""
    from Flowcut.services.zip_parser import parse_zip_structure

    container: AppContainer = request.app.state.container
    _cleanup_expired_zip_uploads(container)

    content = await file.read()

    if len(content) > _MAX_ZIP_SIZE:
        raise HTTPException(413, "zip 文件超过 1024 MB 限制")

    # 校验 zip 格式（不解压，仅验证）
    try:
        with _zipfile.ZipFile(io.BytesIO(content)):
            pass
    except _zipfile.BadZipFile:
        raise HTTPException(400, "无效的 zip 文件")

    # 保存 zip 文件到临时路径；解压推迟到 confirm 步骤
    upload_id = uuid.uuid4().hex
    zip_path = os.path.join(tempfile.gettempdir(), f"flowcut_zip_{upload_id}.zip")
    try:
        with open(zip_path, "wb") as f:
            f.write(content)
    except OSError as e:
        raise HTTPException(500, f"保存 zip 文件失败: {e}")

    # 读取已有 tree 用于匹配
    rows = await container.material_repo.list_by_tenant(
        tenant_key, status="READY", limit=99999,
    )
    existing_tree: dict[str, set[str]] = {}
    for mat in rows:
        p = mat.get("product")
        r = mat.get("scene_role")
        if not p:
            continue
        if p not in existing_tree:
            existing_tree[p] = set()
        if r:
            existing_tree[p].add(r)

    preview = parse_zip_structure(content, existing_tree=existing_tree)

    container.zip_uploads[upload_id] = {
        "tenant_key": tenant_key,
        "zip_path": zip_path,
        "preview": preview,
        "created_at": time.time(),
    }

    return {"upload_id": upload_id, "preview": preview}


class ZipOverride(BaseModel):
    index: int
    product: str
    scene_role: str | None = None


class ConfirmZipRequest(BaseModel):
    upload_id: str
    tenant_key: str | None = None
    overrides: list[ZipOverride] | None = None


@router.post("/upload-zip/confirm")
async def confirm_upload_zip(
    body: ConfirmZipRequest,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """确认 zip 导入：解压、批量上传 OSS、创建 material 记录、入队处理任务。"""
    container: AppContainer = request.app.state.container

    data = container.zip_uploads.get(body.upload_id)
    if data is None:
        raise HTTPException(404, "upload_id 不存在或已过期")
    if data["tenant_key"] != tenant_key:
        raise HTTPException(403, "租户不匹配")

    zip_path = data["zip_path"]
    preview = data["preview"]

    # 解压到临时目录（仅在 confirm 时解压，避免预览阶段浪费磁盘）
    # Custom extract loop: decode filenames properly (CP437 → GBK/UTF-8) AND
    # explicit path-traversal defense (replaces extractall's built-in stripping)
    from Flowcut.services.zip_parser import _decode_zip_filename, _is_mac_metadata

    extracted_dir = tempfile.mkdtemp(prefix=f"flowcut_zip_extract_{body.upload_id}_")
    try:
        with _zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                decoded_name = _decode_zip_filename(info)
                if _is_mac_metadata(decoded_name):
                    continue
                # Build target path using DECODED name, not info.filename
                target_path = os.path.join(extracted_dir, decoded_name)
                # Defense in depth: refuse paths that escape extracted_dir
                abs_target = os.path.abspath(target_path)
                abs_root = os.path.abspath(extracted_dir)
                if not abs_target.startswith(abs_root + os.sep):
                    continue  # skip path-traversal attempts
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with z.open(info) as src, open(target_path, 'wb') as dst:
                    dst.write(src.read())

        material_ids: list[int] = []
        oss_client = build_oss_client()

        # Build override lookup: index → (product, scene_role)
        override_map: dict[int, tuple[str, str | None]] = {}
        if body.overrides:
            for ov in body.overrides:
                override_map[ov.index] = (ov.product, ov.scene_role)

        for idx, item in enumerate(preview):
            if item["status"] == "ignored":
                continue
            # Original names used for filesystem lookup (files extracted under these names)
            original_product = item["product"]
            original_scene_role = item.get("scene_role")
            # Effective names used for OSS key and DB write (may be overridden by user)
            if idx in override_map:
                product, scene_role = override_map[idx]
            else:
                product = original_product
                scene_role = original_scene_role

            safe_original_product = _sanitize_path_component(original_product)
            safe_original_scene_role = (
                _sanitize_path_component(original_scene_role) if original_scene_role else None
            )
            safe_product = _sanitize_path_component(product)
            safe_scene_role = _sanitize_path_component(scene_role) if scene_role else None

            for filename in item["files"]:
                # Filesystem lookup uses ORIGINAL names
                if safe_original_scene_role:
                    src = os.path.join(extracted_dir, safe_original_product, safe_original_scene_role, filename)
                else:
                    src = os.path.join(extracted_dir, safe_original_product, filename)
                if not os.path.isfile(src):
                    continue

                # OSS key + DB write use EFFECTIVE (possibly overridden) names
                oss_key = _make_upload_oss_key(tenant_key, product, filename)
                oss_client.upload(src, oss_key)
                file_size = os.path.getsize(src)
                category = _category_from_filename(filename)

                material = await container.material_repo.create(
                    tenant_key=tenant_key,
                    oss_key=oss_key,
                    oss_url=oss_key,
                    name=filename,
                    category=category,
                    duration=0.0,
                    file_size=file_size,
                    product=safe_product,         # sanitized: strips path-traversal sequences
                    scene_role=safe_scene_role,   # sanitized: strips path-traversal sequences
                )
                material_id = material["id"]
                material_ids.append(material_id)

                envelope = TaskEnvelope(
                    task_type="material_process",
                    payload={
                        "material_id": material_id,
                        "oss_key": oss_key,
                        "oss_url": oss_key,
                    },
                    stream=FlowcutTaskStream.MATERIAL_PROCESS,
                    tenant_key=tenant_key,
                    scope_key=f"material:{material_id}",
                )
                await container.runtime.submit_task(envelope)
    finally:
        # Clean up both the extracted dir and the zip file
        shutil.rmtree(extracted_dir, ignore_errors=True)
        try:
            os.unlink(zip_path)
        except OSError:
            pass
        container.zip_uploads.pop(body.upload_id, None)

    return {"material_ids": material_ids}
