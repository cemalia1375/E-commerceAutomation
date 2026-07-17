"""decompose_video 工具：打开或触发爆款视频拆镜。"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.tools.base import Tool, ToolResult

from Flowcut.runtime.streams import FlowcutTaskStream

if TYPE_CHECKING:
    from simpleclaw.runtime.services import RuntimeServices
    from Flowcut.storage.reference_video_repo import ReferenceVideoRepository
    from Flowcut.storage.script_repo import ScriptRepository


class DecomposeVideoTool(Tool):
    """打开已上传爆款视频的拆镜工作台。

    如果视频已经由上传入口入队，本工具只返回工作台跳转。
    如果视频来自对话框 pending 上传，本工具会预建 PROCESSING 脚本并入队拆镜。
    """

    name = "decompose_video"
    description = (
        "根据已上传视频的 ref_video_id 触发或打开爆款视频拆镜工作台。"
        "适用于用户明确要拆解爆款视频、复刻爆款结构和生成投放脚本的场景。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "ref_video_id": {
                "type": "integer",
                "description": "已上传的参考视频 ID",
            }
        },
        "required": ["ref_video_id"],
    }
    execution_mode = "inline"
    needs_followup = True
    tool_category = "background_write"
    read_only = False
    business_ref_type = "reference_video"
    business_ref_id_field = "ref_video_id"

    def __init__(self, *, runtime: "RuntimeServices",
                 ref_video_repo: "ReferenceVideoRepository",
                 script_repo: "ScriptRepository") -> None:
        self._runtime = runtime
        self._ref_video_repo = ref_video_repo
        self._script_repo = script_repo
        self._tenant_key = ""
        self._session_key = ""

    def set_context(
        self,
        *,
        tenant_key: str = "",
        session_key: str = "",
        **_: object,
    ) -> None:
        self._tenant_key = tenant_key
        self._session_key = session_key

    async def execute(self, ref_video_id: int, **kwargs) -> ToolResult:
        del kwargs
        ref_video = await self._ref_video_repo.get(ref_video_id)
        if ref_video is None:
            return ToolResult(
                content=json.dumps(
                    {"ok": False, "error": f"参考视频 {ref_video_id} 不存在"},
                    ensure_ascii=False,
                ),
                ok=False,
            )

        if self._tenant_key and ref_video.get("tenant_key") != self._tenant_key:
            return ToolResult(
                content=json.dumps(
                    {"ok": False, "error": "该视频不属于当前租户"},
                    ensure_ascii=False,
                ),
                ok=False,
            )

        script_id = ref_video.get("script_id")
        task_id: str | None = None
        if script_id is None:
            script = await self._script_repo.create(
                tenant_key=str(ref_video["tenant_key"]),
                source="decomposed",
                segments=[],
                reference_video_id=ref_video_id,
                product=ref_video.get("product"),
                status="PROCESSING",
            )
            script_id = script["id"]
            await self._ref_video_repo.set_script_id(ref_video_id, script_id)
            await self._ref_video_repo.update_status(ref_video_id, "PROCESSING")
            envelope = TaskEnvelope(
                task_type="scene_decompose",
                payload={
                    "ref_video_id": ref_video_id,
                    "oss_key": ref_video["oss_key"],
                    "oss_url": ref_video["oss_url"],
                    "tenant_key": ref_video["tenant_key"],
                    "workflow_type": "reference_video",
                },
                stream=FlowcutTaskStream.SCENE_DECOMPOSE,
                tenant_key=str(ref_video["tenant_key"]),
                session_key=self._session_key or None,
                scope_key=f"scene_decompose:{ref_video_id}",
            )
            await self._runtime.submit_task(envelope)
            task_id = envelope.task_id
            status = "PROCESSING"
        else:
            status = ref_video.get("status", "UNKNOWN")

        return ToolResult(
            content=json.dumps(
                {
                    "ok": True,
                    "data": {
                        "ref_video_id": ref_video_id,
                        "script_id": script_id,
                        "task_id": task_id,
                        "status": status,
                    },
                    "navigate": {
                        "route": "/workspace/:scriptId",
                        "params": {"scriptId": script_id},
                        "mode": "push",
                    },
                    "ui_hint": {
                        "render_as": "text",
                        "title": "已打开拆镜工作台",
                    },
                },
                ensure_ascii=False,
            )
        )
