"""FFmpeg 拼片 + 评估 Agent 循环。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from simpleclaw.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from simpleclaw.runtime.task_protocol import TaskEnvelope
    from simpleclaw.runtime.services import RuntimeServices


class ComposeVideoTool(Tool):
    """触发后台 FFmpeg 合成任务，将各段素材按脚本顺序拼接成片。"""

    name = "compose_video"
    description = (
        "根据脚本和素材分配方案，触发后台 FFmpeg 拼片任务，"
        "合成完成后由评估 Agent 自动检查质量并决定是否需要重新合成。"
        "任务异步执行，调用后立即返回 task_id，可用 check_task_status 查进度。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "script_id": {
                "type": "integer",
                "description": "已选定的脚本 ID",
            },
            "material_assignments": {
                "type": "array",
                "description": "各段素材分配，每项包含 segment_idx 和 material_id",
                "items": {
                    "type": "object",
                    "properties": {
                        "segment_idx": {"type": "integer"},
                        "material_id": {"type": "integer"},
                    },
                    "required": ["segment_idx", "material_id"],
                },
            },
        },
        "required": ["script_id", "material_assignments"],
    }
    execution_mode = "durable"
    needs_followup = True

    def __init__(self, *, runtime: "RuntimeServices") -> None:
        self._runtime = runtime

    async def prepare_task(
        self,
        script_id: int,
        material_assignments: list[dict],
        **kwargs,
    ) -> "TaskEnvelope | ToolResult":
        """构造 FFmpeg 合成 TaskEnvelope 并提交到后台队列。"""
        raise NotImplementedError("TODO: 构造 TaskEnvelope 并返回")
