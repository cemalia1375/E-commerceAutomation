"""FlowCut 高光资产库 REST 接口。"""

from __future__ import annotations

import os
import re
import tempfile
import time
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from Flowcut.api.deps import require_tenant

from Flowcut.api.container import AppContainer
from Flowcut.storage.oss_client import build_oss_client, get_url_cache

router = APIRouter(prefix="/highlight-assets", tags=["highlight-assets"])

_MAX_UPLOAD_SIZE = 1024 * 1024 * 1024
_VIDEO_EXTS = {"mp4", "mov", "avi", "mkv", "webm", "flv", "wmv"}
_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
_ASSET_TYPES = {"episode_source", "digital_human_connector", "preroll"}


class BatchDeleteHighlightAssetsRequest(BaseModel):
    tenant_key: str | None = None
    asset_ids: list[int] = Field(..., min_length=1, max_length=500)


def _sanitize_path_component(value: str) -> str:
    return re.sub(r"[/\\]|\.{2,}", "_", value.strip()) or "未命名"


def _ext_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _reject_if_not_video(filename: str) -> None:
    ext = _ext_of(filename)
    if ext not in _VIDEO_EXTS:
        raise HTTPException(415, f"不支持的文件类型 .{ext or '(无扩展名)'}；高光资产库仅支持视频")


def _reject_if_not_image(filename: str) -> None:
    ext = _ext_of(filename)
    if ext not in _IMAGE_EXTS:
        raise HTTPException(415, f"不支持的文件类型 .{ext or '(无扩展名)'}；前贴库仅支持图片")


def _make_asset_oss_key(
    tenant_key: str,
    asset_type: str,
    filename: str,
    *,
    drama_name: str | None = None,
    connector_role: str | None = None,
) -> str:
    timestamp = int(time.time())
    safe_tenant = _sanitize_path_component(tenant_key)
    safe_filename = _sanitize_path_component(filename)
    if asset_type == "episode_source":
        group = _sanitize_path_component(drama_name or "未命名剧集")
    elif asset_type == "preroll":
        group = "preroll"
    else:
        group = _sanitize_path_component(connector_role or "通用数字人")
    return f"highlight_assets/{safe_tenant}/{asset_type}/{group}/{timestamp}_{safe_filename}"


def _parse_episode_no_from_filename(filename: str) -> int | None:
    """从文件名（不含扩展名）提取第一串数字作为集数。"""
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    m = re.search(r"\d+", base)
    return int(m.group()) if m else None


def _is_mac_metadata_path(name: str) -> bool:
    if name.startswith("__MACOSX/") or "/__MACOSX/" in name:
        return True
    basename = name.rsplit("/", 1)[-1]
    return basename.startswith("._") or basename == ".DS_Store"


def _decode_zip_name(info: object) -> str:
    """处理 zip 文件名编码（CP437 / UTF-8 / GBK）。"""
    import zipfile as _zf
    info = info  # type: _zf.ZipInfo  # noqa: F841
    if info.flag_bits & 0x800:  # type: ignore[union-attr]
        return info.filename  # type: ignore[union-attr]
    raw = info.filename.encode("cp437", errors="replace")  # type: ignore[union-attr]
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename  # type: ignore[union-attr]


def _normalize_empty(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _resolve_asset_urls(asset: dict) -> dict:
    result = dict(asset)
    oss = build_oss_client()
    cache = get_url_cache()
    val = result.get("oss_url")
    if val and not str(val).startswith("http"):
        result["oss_url"] = cache.get_or_set(str(val), lambda v=str(val): oss.presigned_get_url(v))
    return result


@router.post("/upload")
async def upload_highlight_asset(
    request: Request,
    file: UploadFile = File(...),
    tenant_key: str = Depends(require_tenant),
    asset_type: Literal["episode_source", "digital_human_connector", "preroll"] = Form(...),
    drama_name: str | None = Form(None),
    episode_no: int | None = Form(None),
    connector_role: str | None = Form(None),
):
    container: AppContainer = request.app.state.container

    filename = file.filename or "upload"
    if asset_type == "preroll":
        _reject_if_not_image(filename)
    else:
        _reject_if_not_video(filename)

    drama_name = _normalize_empty(drama_name)
    connector_role = _normalize_empty(connector_role)

    if asset_type not in _ASSET_TYPES:
        raise HTTPException(400, "asset_type 只能是 episode_source、digital_human_connector 或 preroll")
    if asset_type == "episode_source" and not drama_name:
        raise HTTPException(400, "原片资产必须填写 AI 漫剧名称")
    if asset_type == "episode_source" and episode_no is None:
        episode_no = _parse_episode_no_from_filename(filename)
    if asset_type == "digital_human_connector" and not connector_role:
        connector_role = "通用数字人"

    content = await file.read()
    if len(content) > _MAX_UPLOAD_SIZE:
        raise HTTPException(413, "文件超过 1024 MB 限制")
    file_size = len(content)

    oss_key = _make_asset_oss_key(
        tenant_key,
        asset_type,
        filename,
        drama_name=drama_name,
        connector_role=connector_role,
    )

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(content)
        container.oss_client.upload(tmp_path, oss_key)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    asset = await container.highlight_asset_repo.create(
        tenant_key=tenant_key,
        asset_type=asset_type,
        drama_name=drama_name if asset_type == "episode_source" else None,
        episode_no=episode_no if asset_type == "episode_source" else None,
        connector_role=connector_role if asset_type == "digital_human_connector" else None,
        oss_key=oss_key,
        oss_url=oss_key,
        name=filename,
        duration=0.0,
        file_size=file_size,
        metadata={
            "original_filename": filename,
            "upload_source": "highlight_asset_library",
        },
    )
    return _resolve_asset_urls(asset)


@router.post("/upload-zip")
async def upload_highlight_zip(
    request: Request,
    file: UploadFile = File(...),
    tenant_key: str = Depends(require_tenant),
):
    """zip 批量上传原片资产。zip 内顶层文件夹名 = 剧名，文件名自动解析集数。"""
    import io
    import zipfile

    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(415, "请上传 .zip 文件")

    content = await file.read()
    if len(content) > _MAX_UPLOAD_SIZE:
        raise HTTPException(413, "文件超过 1024 MB 限制")

    container: AppContainer = request.app.state.container

    # Parse zip: {drama_name: [(filename, raw_bytes, episode_no)]}
    drama_files: dict[str, list[tuple[str, bytes, int | None]]] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            for info in z.infolist():
                name = _decode_zip_name(info)
                if name.endswith("/") or _is_mac_metadata_path(name):
                    continue
                parts = name.split("/")
                if len(parts) < 2:
                    continue
                filename = parts[-1]
                if _ext_of(filename) not in _VIDEO_EXTS:
                    continue
                drama_name = _sanitize_path_component(parts[0])
                episode_no = _parse_episode_no_from_filename(filename)
                raw = z.read(info.filename)
                drama_files.setdefault(drama_name, []).append((filename, raw, episode_no))
    except zipfile.BadZipFile as exc:
        raise HTTPException(400, f"无效的 zip 文件：{exc}") from exc

    if not drama_files:
        raise HTTPException(422, "zip 中未发现有效视频文件（需放在文件夹内，文件夹名即剧名）")

    created_assets: list[dict] = []
    for drama_name, file_entries in drama_files.items():
        for filename, raw_bytes, episode_no in file_entries:
            oss_key = _make_asset_oss_key(
                tenant_key, "episode_source", filename, drama_name=drama_name
            )
            tmp_path: str | None = None
            try:
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    tmp_path = tmp.name
                    tmp.write(raw_bytes)
                container.oss_client.upload(tmp_path, oss_key)
            finally:
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

            asset = await container.highlight_asset_repo.create(
                tenant_key=tenant_key,
                asset_type="episode_source",
                drama_name=drama_name,
                episode_no=episode_no,
                connector_role=None,
                oss_key=oss_key,
                oss_url=oss_key,
                name=filename,
                duration=0.0,
                file_size=len(raw_bytes),
                metadata={
                    "original_filename": filename,
                    "upload_source": "highlight_zip_upload",
                },
            )
            created_assets.append(_resolve_asset_urls(asset))

    return {
        "ok": True,
        "drama_names": list(drama_files.keys()),
        "created": len(created_assets),
        "assets": created_assets,
    }


@router.get("/groups")
async def list_highlight_asset_groups(
    request: Request,
    tenant_key: str = Depends(require_tenant),
    asset_type: str = Query(...),
):
    """入口层轻量分组：返回 [{name, count}]，按 asset_type 决定分组维度。

    episode_source → 按剧名(drama_name) 分组；digital_human_connector → 按角色(connector_role)。
    """
    if asset_type not in _ASSET_TYPES:
        raise HTTPException(400, "asset_type 只能是 episode_source、digital_human_connector 或 preroll")
    container: AppContainer = request.app.state.container
    group_field = "drama_name" if asset_type == "episode_source" else "connector_role"
    return await container.highlight_asset_repo.list_groups(
        tenant_key,
        asset_type=asset_type,
        group_field=group_field,
    )


@router.get("")
async def list_highlight_assets(
    request: Request,
    tenant_key: str = Depends(require_tenant),
    asset_type: str | None = Query(None),
    drama_name: str | None = Query(None),
    connector_role: str | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    container: AppContainer = request.app.state.container
    if asset_type is not None and asset_type not in _ASSET_TYPES:
        raise HTTPException(400, "asset_type 只能是 episode_source、digital_human_connector 或 preroll")
    assets = await container.highlight_asset_repo.list_by_tenant(
        tenant_key,
        asset_type=asset_type,
        drama_name=_normalize_empty(drama_name),
        connector_role=_normalize_empty(connector_role),
        limit=limit,
        offset=offset,
    )
    return [_resolve_asset_urls(asset) for asset in assets]


@router.delete("/{asset_id}")
async def delete_highlight_asset(
    asset_id: int,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    container: AppContainer = request.app.state.container
    asset = await container.highlight_asset_repo.get(asset_id)
    if asset is None or asset.get("tenant_key") != tenant_key:
        raise HTTPException(404, "高光资产不存在")

    oss_key = asset.get("oss_key")
    if oss_key:
        try:
            container.oss_client.delete_object(str(oss_key))
        except Exception:
            pass
    await container.highlight_asset_repo.delete(asset_id)
    return {"ok": True, "deleted": 1}


@router.post("/batch-delete")
async def batch_delete_highlight_assets(
    body: BatchDeleteHighlightAssetsRequest,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    container: AppContainer = request.app.state.container
    deleted = 0
    skipped: list[int] = []
    errors: list[str] = []

    for asset_id in body.asset_ids:
        asset = await container.highlight_asset_repo.get(asset_id)
        if asset is None or asset.get("tenant_key") != tenant_key:
            skipped.append(asset_id)
            continue

        oss_key = asset.get("oss_key")
        if oss_key:
            try:
                container.oss_client.delete_object(str(oss_key))
            except Exception as exc:
                errors.append(f"{asset_id}: OSS delete failed: {exc}")

        await container.highlight_asset_repo.delete(asset_id)
        deleted += 1

    return {
        "ok": True,
        "deleted": deleted,
        "skipped": skipped,
        "errors": errors,
    }
