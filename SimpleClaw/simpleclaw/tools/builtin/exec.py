"""ExecTool —— 执行 Shell 命令并返回 stdout + stderr。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from simpleclaw.tools.base import Tool, ToolResult

_DEFAULT_TIMEOUT = 30
_MAX_OUTPUT_CHARS = 8000


class ExecTool(Tool):
    name = "exec"
    description = (
        "Execute a shell command and return its output (stdout + stderr combined). "
        "Commands run in the configured workspace directory. "
        "Output is truncated if it exceeds the limit."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute",
            },
            "timeout": {
                "type": "integer",
                "description": f"Timeout in seconds (default: {_DEFAULT_TIMEOUT})",
            },
        },
        "required": ["command"],
    }
    needs_followup = True

    def __init__(self, workspace: str | None = None) -> None:
        self._workspace = str(Path(workspace) if workspace else Path.cwd())

    async def execute(self, *, command: str, timeout: int = _DEFAULT_TIMEOUT) -> ToolResult:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self._workspace,
            )
            try:
                raw, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return ToolResult(
                    content=f"Command timed out after {timeout}s: {command}",
                    ok=False,
                )

            output = raw.decode("utf-8", errors="replace")
            if len(output) > _MAX_OUTPUT_CHARS:
                output = output[:_MAX_OUTPUT_CHARS] + f"\n... (truncated, {len(output)} chars total)"

            exit_code = proc.returncode
            header = f"[exit {exit_code}] $ {command}\n"
            ok = exit_code == 0
            return ToolResult(content=header + output, ok=ok)

        except Exception as exc:
            return ToolResult(content=f"Error executing command: {exc}", ok=False)
