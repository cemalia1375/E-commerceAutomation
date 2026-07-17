"""WriteTool —— 用给定内容创建或覆盖文件。"""

from __future__ import annotations

from pathlib import Path

from simpleclaw.tools.base import Tool, ToolResult


class WriteTool(Tool):
    name = "write"
    description = (
        "Write content to a file, creating it if it does not exist. "
        "Overwrites the existing file completely. "
        "Parent directories are created automatically."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or relative path to the file to write",
            },
            "content": {
                "type": "string",
                "description": "Full content to write to the file",
            },
        },
        "required": ["path", "content"],
    }
    needs_followup = True

    def __init__(self, workspace: str | None = None) -> None:
        self._workspace = Path(workspace) if workspace else Path.cwd()

    async def execute(self, *, path: str, content: str) -> ToolResult:
        target = self._resolve(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except Exception as exc:
            return ToolResult(content=f"Error writing {path}: {exc}", ok=False)

        lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return ToolResult(content=f"Written {lines} lines to {path}")

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self._workspace / p
