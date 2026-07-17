"""素材匹配服务 — 段级双向量语义搜索逻辑。

被 SearchMaterialsTool（Agent 工具）和 POST /materials/match（REST 路由）共享，
保证两个入口的匹配规则一致。
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from Flowcut.services.embedding import EmbeddingService
    from Flowcut.storage.material_repo import MaterialRepository
    from Flowcut.storage.oss_client import OSSClient
    from Flowcut.storage.vector_store import VectorStore


async def match_segment(
    seg: dict,
    *,
    tenant_key: str,
    product: str,
    embedding_service: "EmbeddingService",
    vector_store: "VectorStore",
    material_repo: "MaterialRepository",
    oss_client: "OSSClient | None" = None,
    limit: int = 3,
) -> dict:
    """对单个脚本段执行两阶段语义搜索。

    Returns:
        {
            "phase1": [candidate, ...],  # 产品专属命中
            "phase2": [candidate, ...],  # 通用兜底
            "error": str | None,
        }
        candidate = {material_id, name, duration, product, scene_role, score,
                     preview_url, category}
    """
    visual = (seg.get("visual") or "").strip()
    copy = (seg.get("copy") or "").strip()

    if not visual and not copy:
        return {"phase1": [], "phase2": [], "error": "段缺 visual 和 copy"}

    visual_vec: list[float] | None = None
    copy_vec: list[float] | None = None
    try:
        if visual:
            visual_vec = await embedding_service.embed(visual)
        if copy:
            copy_vec = await embedding_service.embed(copy)
    except Exception as exc:
        return {"phase1": [], "phase2": [], "error": f"embedding 失败：{exc}"}

    # 阶段一：产品专属搜索
    try:
        raw = await vector_store.search(
            visual_vec, copy_vec,
            tenant_key=tenant_key,
            product=product,
            limit=limit,
        )
        phase1 = await _resolve_materials(raw, material_repo, oss_client=oss_client)
    except Exception as exc:
        return {"phase1": [], "phase2": [], "error": f"阶段一失败：{exc}"}

    # 阶段二：通用兜底（仅当阶段一不足）
    phase2: list[dict] = []
    if len(phase1) < limit:
        need = limit - len(phase1)
        try:
            raw2 = await vector_store.search(
                visual_vec, copy_vec,
                tenant_key=tenant_key,
                product=None,
                limit=need,
            )
            phase1_ids = {r["material_id"] for r in phase1}
            phase2 = await _resolve_materials(
                [(mid, sc) for mid, sc in raw2 if mid not in phase1_ids],
                material_repo,
                oss_client=oss_client,
            )
        except Exception:
            pass

    return {"phase1": phase1, "phase2": phase2, "error": None}


async def match_segments_parallel(
    segments: list[dict],
    *,
    tenant_key: str,
    product: str,
    embedding_service: "EmbeddingService",
    vector_store: "VectorStore",
    material_repo: "MaterialRepository",
    oss_client: "OSSClient | None" = None,
    limit: int = 3,
) -> list[dict]:
    """批量并行匹配，返回与输入等长的结果数组。

    每个元素结构：
        {
            "seg_idx": int,
            "visual": str,
            "copy": str,
            "phase1": [...],
            "phase2": [...],
            "error": str | None,
        }
    """
    tasks = [
        match_segment(
            seg,
            tenant_key=tenant_key,
            product=product,
            embedding_service=embedding_service,
            vector_store=vector_store,
            material_repo=material_repo,
            oss_client=oss_client,
            limit=limit,
        )
        for seg in segments
    ]
    results = await asyncio.gather(*tasks)

    out: list[dict] = []
    for idx, (seg, result) in enumerate(zip(segments, results)):
        out.append({
            "seg_idx": int(seg.get("idx", idx)),
            "visual": seg.get("visual") or seg.get("content", ""),
            "copy": seg.get("copy", ""),
            "phase1": result["phase1"],
            "phase2": result["phase2"],
            "error": result["error"],
        })
    return out


async def _resolve_materials(
    scored: list[tuple[int, float]],
    material_repo: "MaterialRepository",
    *,
    oss_client: "OSSClient | None" = None,
) -> list[dict]:
    """将 [(material_id, score), ...] 批量解析为完整素材信息。"""
    if not scored:
        return []

    tasks = [material_repo.get(mid) for mid, _ in scored]
    materials = await asyncio.gather(*tasks)

    out: list[dict] = []
    for (mid, score), mat in zip(scored, materials):
        if mat is None:
            continue

        preview_url: str | None = None
        oss_url_field = mat.get("oss_url") or mat.get("oss_key")
        if oss_client is not None and oss_url_field and not str(oss_url_field).startswith("http"):
            try:
                preview_url = oss_client.presigned_get_url(oss_url_field)
            except Exception:
                preview_url = None
        elif oss_url_field and str(oss_url_field).startswith("http"):
            preview_url = oss_url_field

        out.append({
            "material_id": mat["id"],
            "name": mat.get("name", ""),
            "duration": mat.get("duration", 0),
            "product": mat.get("product"),
            "scene_role": mat.get("scene_role"),
            "category": mat.get("category", ""),
            "score": float(score),
            "preview_url": preview_url,
        })
    return out
