"""脚本相关路由：/flowcut/scripts/..."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from Flowcut.api.deps import require_tenant

from Flowcut.storage.script_repo import StatusConflictError  # noqa: F401
from Flowcut.tools.upload_script import UploadScriptTool
from Flowcut.tools.update_script import UpdateScriptTool
from Flowcut.tools.match_by_script import MatchByScriptTool
from Flowcut.tools.export_package import ExportPackageTool

router = APIRouter(prefix="/flowcut/scripts", tags=["flowcut-scripts"])

_PREVIEW_CACHE_DIR = os.path.join(tempfile.gettempdir(), "flowcut_segment_previews")


class SaveHighlightCreativeRequest(BaseModel):
    tenant_key: str | None = None
    creative_type: str = "highlight_original"


def _c(request: Request):
    return request.app.state.container


def _clip_cache_path(script_id: int, seg_idx: int, start: float, end: float) -> str:
    safe_start = int(round(start * 1000))
    safe_end = int(round(end * 1000))
    return os.path.join(
        _PREVIEW_CACHE_DIR,
        f"script_{script_id}_seg_{seg_idx}_{safe_start}_{safe_end}.mp4",
    )


def _source_cache_path(ref_video_id: int) -> str:
    return os.path.join(_PREVIEW_CACHE_DIR, f"ref_{ref_video_id}.mp4")


def _run_ffmpeg_clip(source_path: str, output_path: str, start: float, end: float) -> None:
    env_path = os.environ.get("FFMPEG_PATH", "").strip()
    if env_path:
        if os.path.isabs(env_path):
            ffmpeg = env_path
        else:
            ffmpeg = shutil.which(env_path) or shutil.which("ffmpeg")
    else:
        ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found. Please install ffmpeg first.")
    duration = max(0.1, float(end) - float(start))
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp_output = output_path + ".tmp.mp4"
    if os.path.exists(tmp_output):
        os.unlink(tmp_output)
    result = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-ss",
            f"{max(0.0, float(start)):.3f}",
            "-i",
            source_path,
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            tmp_output,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffmpeg clip failed")
    os.replace(tmp_output, output_path)


@router.post("")
async def upload_script(
    request: Request,
    tenant_key: str = Depends(require_tenant),
) -> dict[str, Any]:
    payload = await request.json()
    segments = payload.get("segments") or []

    c = _c(request)
    tool = UploadScriptTool(script_repo=c.script_repo)
    result = await tool.execute(tenant_key=tenant_key, segments=segments)
    if not result.ok:
        raise HTTPException(422, result.content)
    m = re.search(r"script_id=(\d+)", result.content)
    return {"ok": True, "script_id": int(m.group(1)) if m else None}


@router.get("")
async def list_scripts(
    request: Request,
    tenant_key: str = Depends(require_tenant),
    source: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    c = _c(request)
    scripts = await c.script_repo.list_by_tenant(
        tenant_key, source=source, status=status
    )
    return {"ok": True, "scripts": scripts}


@router.get("/{script_id}")
async def get_script(
    request: Request,
    script_id: int,
    tenant_key: str = Depends(require_tenant),
) -> dict[str, Any]:
    c = _c(request)
    script = await c.script_repo.get(script_id)
    if script is None or script.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"script {script_id} not found")
    return {"ok": True, **script}


@router.get("/{script_id}/segments/{seg_idx}/preview.mp4")
async def preview_script_segment(
    request: Request,
    script_id: int,
    seg_idx: int,
    tenant_key: str = Depends(require_tenant),
) -> FileResponse:
    """返回脚本某一段的原视频截取预览。

    仅用于工作台预览：第一次请求会下载原视频并用 ffmpeg 截取，后续命中本地缓存。
    """
    c = _c(request)
    script = await c.script_repo.get(script_id)
    if script is None or script.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"script {script_id} not found")
    segments = script.get("segments") or []
    if seg_idx < 0 or seg_idx >= len(segments):
        raise HTTPException(404, f"segment {seg_idx} not found")
    ref_video_id = script.get("reference_video_id")
    if ref_video_id is None:
        raise HTTPException(400, "script has no reference_video_id")
    ref_video = await c.ref_video_repo.get(int(ref_video_id))
    if ref_video is None:
        raise HTTPException(404, f"reference video {ref_video_id} not found")

    seg = segments[seg_idx]
    try:
        start = float(seg.get("start_time", 0))
        end = float(seg.get("end_time", start + 0.1))
    except (TypeError, ValueError):
        raise HTTPException(422, "invalid segment time range") from None
    if end <= start:
        raise HTTPException(422, "invalid segment time range")

    os.makedirs(_PREVIEW_CACHE_DIR, exist_ok=True)
    source_path = _source_cache_path(int(ref_video_id))
    output_path = _clip_cache_path(script_id, seg_idx, start, end)

    try:
        if not os.path.exists(source_path):
            oss_key = str(ref_video.get("oss_key") or ref_video.get("oss_url") or "")
            if not oss_key:
                raise RuntimeError("reference video has no oss_key")
            c.oss_client.download(oss_key, source_path)
        if not os.path.exists(output_path):
            _run_ffmpeg_clip(source_path, output_path, start, end)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"segment preview failed: {exc}") from exc

    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename=f"script_{script_id}_seg_{seg_idx}.mp4",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.patch("/{script_id}")
async def update_script(
    request: Request,
    script_id: int,
    tenant_key: str = Depends(require_tenant),
) -> dict[str, Any]:
    payload = await request.json()
    segments = payload.get("segments") or []
    c = _c(request)
    script = await c.script_repo.get(script_id)
    if script is None or script.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"script {script_id} not found")
    tool = UpdateScriptTool(script_repo=c.script_repo)
    result = await tool.execute(script_id=script_id, segments=segments)
    if not result.ok:
        code = 409 if "DRAFT" in result.content else 422
        raise HTTPException(code, result.content)
    return {"ok": True}


@router.post("/{script_id}/confirm")
async def confirm_script(
    request: Request,
    script_id: int,
    tenant_key: str = Depends(require_tenant),
) -> dict[str, Any]:
    c = _c(request)
    script = await c.script_repo.get(script_id)
    if script is None or script.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"script {script_id} not found")
    await c.script_repo.update_status(script_id, "CONFIRMED")
    return {"ok": True, "status": "CONFIRMED"}


@router.post("/{script_id}/save-highlight-creative")
async def save_highlight_creative(
    request: Request,
    script_id: int,
    body: SaveHighlightCreativeRequest,
    tenant_key: str = Depends(require_tenant),
) -> dict[str, Any]:
    c = _c(request)
    script = await c.script_repo.get(script_id)
    if script is None or script.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"script {script_id} not found")

    creative_type = body.creative_type
    if creative_type not in {"highlight_original", "highlight_digital_human"}:
        raise HTTPException(
            422,
            "creative_type must be highlight_original or highlight_digital_human",
        )

    best_segment = _pick_highlight_segment(script.get("segments") or [])
    if best_segment is None:
        raise HTTPException(422, "没有找到可保存的高光候选段")

    existing = await c.creative_repo.find_highlight_by_script(
        tenant_key=tenant_key,
        script_id=script_id,
        creative_type=creative_type,
    )
    if existing is None:
        creative = await c.creative_repo.create_highlight_job(
            tenant_key=tenant_key,
            session_key="highlight_workspace",
            script_id=script_id,
            creative_type=creative_type,
            batch_id=f"single:{script_id}",
            source_asset_id=None,
            connector_asset_id=None,
        )
        creative_id = int(creative["id"])
    else:
        creative_id = int(existing["id"])

    highlight_start = float(best_segment.get("start_time", 0))
    highlight_end = float(best_segment.get("end_time", 0))
    compose_strategy = (
        "original_starts_with_highlight_no_duplicate"
        if creative_type == "highlight_original" and highlight_start <= 0.35
        else "frontload_highlight_then_followup"
    )
    compose_plan = {
        "workflow_type": "highlight_extract",
        "continuation_type": "digital_human"
        if creative_type == "highlight_digital_human"
        else "original",
        "script_id": script_id,
        "ref_video_id": script.get("reference_video_id"),
        "stage": "analysis_saved",
        "next_step": "compose_video_pending",
        "compose_strategy": compose_strategy,
    }
    highlight_reason = {
        "idx": best_segment.get("idx"),
        "candidate_use": best_segment.get("candidate_use"),
        "hook_strength": best_segment.get("hook_strength"),
        "ending_connectability": best_segment.get("ending_connectability"),
        "context_dependency": best_segment.get("context_dependency"),
        "continuity_risk": best_segment.get("continuity_risk"),
        "ending_state": best_segment.get("ending_state"),
        "open_question": best_segment.get("open_question"),
        "bridge_text": best_segment.get("bridge_text"),
        "reason": best_segment.get("reason"),
        "followup_fit": best_segment.get("followup_fit"),
        "frontload_recommendation": (
            "原片开头已是高光，生成高光+原片时不重复拼接，直接输出原片。"
            if compose_strategy == "original_starts_with_highlight_no_duplicate"
            else "该高光适合前置，再接回原片或指定衔接素材。"
        ),
    }
    await c.creative_repo.mark_highlight_ready(
        creative_id,
        highlight_start=highlight_start,
        highlight_end=highlight_end,
        highlight_reason=highlight_reason,
        compose_plan=compose_plan,
    )
    return {
        "ok": True,
        "creative_id": creative_id,
        "highlight_start": highlight_start,
        "highlight_end": highlight_end,
        "compose_strategy": compose_strategy,
    }


@router.post("/{script_id}/reopen")
async def reopen_script(
    request: Request,
    script_id: int,
    tenant_key: str = Depends(require_tenant),
) -> dict[str, Any]:
    c = _c(request)
    script = await c.script_repo.get(script_id)
    if script is None or script.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"script {script_id} not found")
    await c.script_repo.update_status(script_id, "DRAFT")
    return {"ok": True, "status": "DRAFT"}


def _score_highlight_segment(seg: dict) -> float:
    def num(key: str) -> float:
        try:
            return float(seg.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    candidate_bonus = {
        "primary_hook": 100.0,
        "secondary_hook": 60.0,
        "context_only": 0.0,
        "reject": -100.0,
    }.get(str(seg.get("candidate_use") or ""), 0.0)
    return (
        candidate_bonus
        + num("hook_strength") * 10
        + num("ending_connectability") * 6
        - num("context_dependency") * 4
        - num("continuity_risk") * 5
    )


def _pick_highlight_segment(segments: list[dict]) -> dict | None:
    candidates = [
        seg
        for seg in segments
        if str(seg.get("candidate_use") or "") in {"primary_hook", "secondary_hook"}
    ]
    if not candidates:
        candidates = [seg for seg in segments if float(seg.get("hook_strength") or 0) >= 7]
    if not candidates:
        return None
    return max(candidates, key=_score_highlight_segment)


@router.post("/{script_id}/match")
async def match_script(
    request: Request,
    script_id: int,
    tenant_key: str = Depends(require_tenant),
) -> dict[str, Any]:
    payload = await request.json()
    product = payload.get("product") or ""

    c = _c(request)
    script = await c.script_repo.get(script_id)
    if script is None or script.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"script {script_id} not found")
    tool = MatchByScriptTool(
        script_repo=c.script_repo,
        embedding_service=c.embedding_service,
        vector_store=c.vector_store,
        material_repo=c.material_repo,
        oss_client=c.oss_client,
    )
    result = await tool.execute(
        script_id=script_id, tenant_key=tenant_key, product=product,
    )
    if not result.ok:
        raise HTTPException(400, result.content)
    data = json.loads(result.content)
    return {"ok": True, **data}


@router.post("/{script_id}/export")
async def export_script(
    request: Request,
    script_id: int,
    tenant_key: str = Depends(require_tenant),
) -> dict[str, Any]:
    """触发导出任务。

    接受新字段 ``selections: {seg_idx: [material_id, ...]}``；
    同时兼容旧字段 ``material_ids: [int]``（无段信息，全部 normalize 到
    段 "0"）。selections 优先于 material_ids。
    """
    payload = await request.json()
    c = _c(request)
    script = await c.script_repo.get(script_id)
    if script is None or script.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"script {script_id} not found")

    raw_selections = payload.get("selections")
    if raw_selections is not None:
        if not isinstance(raw_selections, dict):
            raise HTTPException(422, "selections 必须是 dict")
        selections = {}
        for k, v in raw_selections.items():
            seg_key = str(k)
            if v is None:
                selections[seg_key] = []
                continue
            # bool 是 int 子类，需显式排除
            if not isinstance(v, list) or isinstance(v, bool):
                raise HTTPException(
                    422,
                    f"selections[{seg_key}] 必须是 list（期望 list[int]）",
                )
            for elem in v:
                if not isinstance(elem, int) or isinstance(elem, bool):
                    raise HTTPException(
                        422,
                        f"selections[{seg_key}] 元素必须是 int（收到 {type(elem).__name__}）",
                    )
            selections[seg_key] = list(v)
    else:
        legacy_ids = payload.get("material_ids") or []
        if not legacy_ids:
            raise HTTPException(422, "selections 或 material_ids 至少传一个")
        selections = {"0": list(legacy_ids)}

    if not selections or not any(v for v in selections.values()):
        raise HTTPException(422, "selections 不能为空")

    tool = ExportPackageTool(runtime=c.runtime)
    envelope = await tool.prepare_task(
        script_id=script_id,
        selections=selections,
        tenant_key=tenant_key,
    )
    await c.runtime.submit_task(envelope)
    return {"ok": True, "task_id": envelope.task_id}


class UpdateProductBody(BaseModel):
    product: str | None = None


@router.post("/{script_id}/update-product")
async def update_script_product(
    script_id: int,
    body: UpdateProductBody,
    request: Request,
    tenant_key: str = Depends(require_tenant),
) -> dict[str, Any]:
    """修改脚本绑定的产品。空字符串等同 null（清空）。"""
    c = _c(request)
    script = await c.script_repo.get(script_id)
    if script is None or script.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"script {script_id} not found")
    normalized = body.product.strip() if body.product else None
    if normalized == "":
        normalized = None
    try:
        await c.script_repo.update_product(script_id, normalized)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True, "script_id": script_id, "product": normalized}
