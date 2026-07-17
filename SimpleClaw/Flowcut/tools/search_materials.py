"""按脚本段搜素材库，返回三档结果（双向量 max 融合语义搜索）。

匹配核心逻辑在 Flowcut.services.material_matcher，本工具仅负责把
结构化结果格式化为给 Agent 的文本输出。
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from simpleclaw.tools.base import Tool, ToolResult

from Flowcut.services.material_matcher import match_segments_parallel

if TYPE_CHECKING:
    from Flowcut.storage.material_repo import MaterialRepository
    from Flowcut.storage.script_repo import ScriptRepository
    from Flowcut.storage.vector_store import VectorStore
    from Flowcut.services.embedding import EmbeddingService


class SearchMaterialsTool(Tool):
    """按已选脚本的各段需求搜索素材库，返回三档候选素材。"""

    name = "search_materials"
    description = (
        "根据已选定的脚本 ID，为每个脚本段在素材库中搜索匹配素材，"
        "返回三档候选（最优、次优、备选）供用户确认或 Agent 自动选择。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "script_id": {
                "type": "integer",
                "description": "已选定的脚本 ID",
            },
            "product": {
                "type": "string",
                "description": "当前产品名；省略或为空字符串时使用脚本绑定的 product，都为空则报错。",
            },
        },
        "required": ["script_id"],
    }
    execution_mode = "inline"
    needs_followup = True

    def __init__(self, *,
                 material_repo: "MaterialRepository",
                 script_repo: "ScriptRepository",
                 vector_store: "VectorStore",
                 embedding_service: "EmbeddingService") -> None:
        self._material_repo = material_repo
        self._script_repo = script_repo
        self._vector_store = vector_store
        self._embedding_service = embedding_service

    async def execute(
        self, script_id: int, product: str = "", **kwargs,
    ) -> ToolResult:
        script = await self._script_repo.get(script_id)
        if script is None:
            return ToolResult(content=f"脚本 {script_id} 不存在", ok=False)

        # product 三段回退：caller 显式值 > 脚本绑定 > 报错
        effective_product = (product or "").strip()
        if not effective_product:
            effective_product = (script.get("product") or "").strip()
        if not effective_product:
            return ToolResult(
                content="请先为脚本选择产品（或在工具调用时显式传 product）",
                ok=False,
            )

        raw_segments = script.get("segments_json") or script.get("segments")
        if not raw_segments:
            return ToolResult(content="脚本段为空，无法搜索", ok=False)

        segments: list[dict] = (
            json.loads(raw_segments) if isinstance(raw_segments, str) else raw_segments
        )

        results = await match_segments_parallel(
            segments,
            tenant_key=script["tenant_key"],
            product=effective_product,
            embedding_service=self._embedding_service,
            vector_store=self._vector_store,
            material_repo=self._material_repo,
        )

        lines: list[str] = []
        for seg_result in results:
            seg_idx = seg_result["seg_idx"]
            visual = seg_result["visual"]
            copy = seg_result["copy"]
            lines.append(f"脚本段 {seg_idx}「画面：{visual} / 文案：{copy}」")

            if seg_result["error"]:
                lines.append(f"  ⚠ 搜索失败：{seg_result['error']}\n")
                continue

            phase1 = seg_result["phase1"]
            phase2 = seg_result["phase2"]

            for rank, item in enumerate(phase1):
                label = ["✅ 最优", "▸ 次优", "○ 备选"][rank] if rank < 3 else "  •"
                source_tag = f"[{item.get('product') or '通用'}]"
                lines.append(
                    f"  {label}  素材 #{item['material_id']} "
                    f"[{item.get('duration', 0)}s] "
                    f"{item.get('name', '')}   "
                    f"相似度 {item['score']:.2f}  {source_tag}"
                )

            if phase2:
                lines.append("  ── 通用兜底素材 ──")
                for rank, item in enumerate(phase2):
                    label = ["✅ 最优", "▸ 次优", "○ 备选"][rank] if rank < 3 else "  •"
                    lines.append(
                        f"  {label}  素材 #{item['material_id']} "
                        f"[{item.get('duration', 0)}s] "
                        f"{item.get('name', '')}   "
                        f"相似度 {item['score']:.2f}  [通用]"
                    )

            if not phase1 and not phase2:
                fallback = await self._material_repo.list_by_tenant(
                    script["tenant_key"],
                    limit=3,
                    status="READY",
                )
                if fallback:
                    lines.append("  ⚠ 未找到语义匹配，按分类兜底：")
                    for fb in fallback:
                        lines.append(
                            f"    • 素材 #{fb['id']} [{fb.get('duration', 0)}s] "
                            f"{fb.get('name', '')}"
                        )
                else:
                    lines.append("  ⚠ 未找到任何可用素材")

            lines.append("")

        return ToolResult(
            content="\n".join(lines),
            ok=True,
            metadata={
                "tenant_key": script["tenant_key"],
                "segments_count": len(results),
            },
        )
