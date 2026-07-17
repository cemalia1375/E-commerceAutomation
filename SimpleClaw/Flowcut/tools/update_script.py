"""UpdateScriptTool — 更新脚本 segments（仅 DRAFT）。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from simpleclaw.tools.base import Tool, ToolResult

# 共用校验 / 归一化函数，避免在 update_script.py 里重复实现
from Flowcut.tools.upload_script import _normalize_segment, _validate_segments

if TYPE_CHECKING:
    from Flowcut.storage.script_repo import ScriptRepository


class UpdateScriptTool(Tool):
    name = "update_script"
    description = "更新脚本的 segments；仅当 status=DRAFT 允许更新。"
    parameters = {
        "type": "object",
        "properties": {
            "script_id": {"type": "integer"},
            "segments": {"type": "array"},
        },
        "required": ["script_id", "segments"],
    }
    execution_mode = "inline"
    needs_followup = True

    def __init__(self, *, script_repo: "ScriptRepository") -> None:
        self._repo = script_repo

    async def execute(
        self, script_id: int, segments: list[dict], **kwargs
    ) -> ToolResult:
        err = _validate_segments(segments)
        if err:
            return ToolResult(content=err, ok=False)

        normalized = [_normalize_segment(s, i) for i, s in enumerate(segments)]
        try:
            await self._repo.update_segments(script_id, normalized)
        except Exception as exc:
            return ToolResult(
                content=f"更新失败：{exc}（请先确认脚本状态为 DRAFT）",
                ok=False,
            )
        return ToolResult(content=f"脚本 {script_id} 已更新", ok=True)
