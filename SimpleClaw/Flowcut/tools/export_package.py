"""ExportPackageTool — 打包脚本+素材+音频+原视频为 zip，异步任务。"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.tools.base import Tool

from Flowcut.runtime.streams import FlowcutTaskStream

if TYPE_CHECKING:
    from simpleclaw.runtime.services import RuntimeServices


class ExportPackageTool(Tool):
    name = "export_package"
    description = (
        "把脚本 + 选中素材 + 音频 + 原爆款视频打包成 zip，异步任务。"
        "成功后返回 task_id，前端轮询 /flowcut/tasks/{task_id} 拿下载链接。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "script_id": {"type": "integer"},
            "selections": {
                "type": "object",
                "description": (
                    "key 为段 idx 字符串，value 为该段勾选的 material_id 列表"
                    "（按勾选顺序）。"
                ),
                "additionalProperties": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
            },
            "tenant_key": {"type": "string"},
        },
        "required": ["script_id", "selections", "tenant_key"],
    }
    execution_mode = "durable"
    needs_followup = True

    def __init__(self, *, runtime: "RuntimeServices") -> None:
        self._runtime = runtime

    async def prepare_task(
        self,
        script_id: int,
        selections: dict[str, list[int]],
        tenant_key: str,
        **kwargs: Any,
    ) -> TaskEnvelope:
        """构造 EXPORT_PACKAGE TaskEnvelope。

        payload 同时塞 ``selections`` 和 ``material_ids``（去重 flatten）:
        - ``selections`` 是新格式，Task 5 之后 executor 切到它；
        - ``material_ids`` 是临时兼容字段，便于老 executor 继续工作。
        """
        if not selections:
            raise ValueError("selections 不能为空")
        flat: list[int] = []
        seen: set[int] = set()
        for mids in selections.values():
            for mid in mids:
                if mid not in seen:
                    seen.add(mid)
                    flat.append(mid)
        if not flat:
            raise ValueError("selections 中所有段均为空")

        task_id = f"export-{uuid.uuid4().hex[:12]}"
        return TaskEnvelope(
            task_id=task_id,
            task_type="export_package",
            tenant_key=tenant_key,
            stream=FlowcutTaskStream.EXPORT_PACKAGE,
            scope_key=f"export:{script_id}:{task_id}",
            payload={
                "script_id": script_id,
                "selections": {k: list(v) for k, v in selections.items()},
                "material_ids": flat,
            },
        )
