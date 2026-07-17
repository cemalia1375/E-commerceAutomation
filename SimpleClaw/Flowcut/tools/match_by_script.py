"""MatchByScriptTool — 按脚本驱动素材召回。"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from simpleclaw.tools.base import Tool, ToolResult

from Flowcut.services.material_matcher import match_segments_parallel

if TYPE_CHECKING:
    from Flowcut.services.embedding import EmbeddingService
    from Flowcut.storage.material_repo import MaterialRepository
    from Flowcut.storage.oss_client import OSSClient
    from Flowcut.storage.script_repo import ScriptRepository
    from Flowcut.storage.vector_store import VectorStore


class MatchByScriptTool(Tool):
    name = "match_by_script"
    description = "根据已确认的脚本（status=CONFIRMED）逐段召回素材。"
    parameters = {
        "type": "object",
        "properties": {
            "script_id": {"type": "integer"},
            "product": {"type": "string"},
            "tenant_key": {"type": "string"},
        },
        "required": ["script_id", "tenant_key"],
    }
    execution_mode = "inline"
    needs_followup = True

    def __init__(
        self,
        *,
        script_repo: "ScriptRepository",
        embedding_service: "EmbeddingService",
        vector_store: "VectorStore",
        material_repo: "MaterialRepository",
        oss_client: "OSSClient | None",
    ) -> None:
        self._repo = script_repo
        self._embedding = embedding_service
        self._vector_store = vector_store
        self._material_repo = material_repo
        self._oss = oss_client

    async def execute(
        self,
        script_id: int,
        tenant_key: str,
        product: str = "",
        **kwargs,
    ) -> ToolResult:
        script = await self._repo.get(script_id)
        if script is None:
            return ToolResult(content=f"脚本 {script_id} 不存在", ok=False)
        if script["status"] != "CONFIRMED":
            return ToolResult(
                content=f"脚本 {script_id} 状态={script['status']}，请先 CONFIRMED",
                ok=False,
            )

        results = await match_segments_parallel(
            script["segments"],
            tenant_key=tenant_key,
            product=product,
            embedding_service=self._embedding,
            vector_store=self._vector_store,
            material_repo=self._material_repo,
            oss_client=self._oss,
        )

        return ToolResult(
            content=json.dumps({"results": results}, ensure_ascii=False),
            ok=True,
        )
