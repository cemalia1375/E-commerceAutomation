"""UploadScriptTool — 创建用户上传脚本（source=uploaded）。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from simpleclaw.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from Flowcut.storage.script_repo import ScriptRepository


def _normalize_segment(seg: dict, idx: int) -> dict:
    visual = (seg.get("visual") or "").strip()
    copy = (seg.get("copy") or "").strip()
    return {
        "idx": idx,
        "start_time": float(seg.get("start_time") or 0.0),
        "end_time": float(seg.get("end_time") or 0.0),
        "visual": visual,
        "copy": copy,
    }


def _validate_segments(segments: list[dict]) -> str | None:
    if not segments:
        return "脚本至少需要一段"
    for i, seg in enumerate(segments):
        if not (seg.get("visual") or "").strip() and not (seg.get("copy") or "").strip():
            return f"第 {i} 段 visual 和 copy 都为空"
    return None


class UploadScriptTool(Tool):
    name = "upload_script"
    description = "上传一份用户编写的脚本（含画面与文案），创建后进入 DRAFT 状态。"
    parameters = {
        "type": "object",
        "properties": {
            "tenant_key": {"type": "string"},
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "visual": {"type": "string"},
                        "copy": {"type": "string"},
                        "start_time": {"type": "number"},
                        "end_time": {"type": "number"},
                    },
                },
            },
        },
        "required": ["tenant_key", "segments"],
    }
    execution_mode = "inline"
    needs_followup = True

    def __init__(self, *, script_repo: "ScriptRepository") -> None:
        self._repo = script_repo

    async def execute(
        self, tenant_key: str, segments: list[dict], **kwargs
    ) -> ToolResult:
        err = _validate_segments(segments)
        if err:
            return ToolResult(content=err, ok=False)

        normalized = [_normalize_segment(s, i) for i, s in enumerate(segments)]
        record = await self._repo.create(
            tenant_key=tenant_key,
            source="uploaded",
            segments=normalized,
        )
        return ToolResult(
            content=f"脚本已创建：script_id={record['id']}",
            ok=True,
        )
