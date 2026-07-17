"""EditTool —— 在文件中替换精确匹配的字符串（查找并替换）。"""

from __future__ import annotations

from pathlib import Path

from simpleclaw.tools.base import Tool, ToolResult


class EditTool(Tool):
    name = "edit"
    description = (
        "Replace an exact occurrence of old_string with new_string in a file. "
        "old_string must match the file content exactly (including whitespace and indentation). "
        "The file must already exist. "
        "Use replace_all=true to replace every occurrence instead of just the first."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to edit",
            },
            "old_string": {
                "type": "string",
                "description": "The exact text to find in the file",
            },
            "new_string": {
                "type": "string",
                "description": "The text to replace it with",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences (default: false — replace only the first)",
            },
        },
        "required": ["path", "old_string", "new_string"],
    }
    needs_followup = True

    def __init__(self, workspace: str | None = None) -> None:
        self._workspace = Path(workspace) if workspace else Path.cwd()

    async def execute(
        self,
        *,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> ToolResult:
        target = self._resolve(path)

        try:
            original = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ToolResult(content=f"Error: file not found: {path}", ok=False)
        except Exception as exc:
            return ToolResult(content=f"Error reading {path}: {exc}", ok=False)

        count = original.count(old_string)
        if count == 0:
            return ToolResult(
                content=f"Error: old_string not found in {path}",
                ok=False,
            )
        if count > 1 and not replace_all:
            return ToolResult(
                content=(
                    f"Error: old_string appears {count} times in {path}. "
                    "Provide more context to make it unique, or set replace_all=true."
                ),
                ok=False,
            )

        updated = original.replace(old_string, new_string) if replace_all \
                  else original.replace(old_string, new_string, 1)

        try:
            target.write_text(updated, encoding="utf-8")
        except Exception as exc:
            return ToolResult(content=f"Error writing {path}: {exc}", ok=False)

        replacements = count if replace_all else 1
        return ToolResult(content=f"Replaced {replacements} occurrence(s) in {path}")

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self._workspace / p
