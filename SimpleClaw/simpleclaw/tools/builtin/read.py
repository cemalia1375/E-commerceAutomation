"""ReadTool —— 读取文件内容并返回给 LLM。"""

from __future__ import annotations

from pathlib import Path

from simpleclaw.tools.base import Tool, ToolResult


class ReadTool(Tool):
    name = "read"
    description = (
        "Read the contents of a file. "
        "Returns the file content as text. "
        "Use offset and limit to read a specific range of lines."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or relative path to the file",
            },
            "offset": {
                "type": "integer",
                "description": "Line number to start reading from (1-based). Omit to read from the beginning.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to read. Omit to read the whole file.",
            },
        },
        "required": ["path"],
    }
    needs_followup = True

    def __init__(self, workspace: str | None = None) -> None:
        self._workspace = Path(workspace) if workspace else Path.cwd()

    async def execute(self, *, path: str, offset: int | None = None, limit: int | None = None) -> ToolResult:
        target = self._resolve(path)
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ToolResult(content=f"Error: file not found: {path}", ok=False)
        except Exception as exc:
            return ToolResult(content=f"Error reading {path}: {exc}", ok=False)

        lines = text.splitlines(keepends=True)
        start = max(0, (offset or 1) - 1)
        end   = start + limit if limit else len(lines)
        selected = lines[start:end]

        header = f"[{path}  lines {start+1}–{min(end, len(lines))} / {len(lines)}]\n"
        return ToolResult(content=header + "".join(selected))

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self._workspace / p
