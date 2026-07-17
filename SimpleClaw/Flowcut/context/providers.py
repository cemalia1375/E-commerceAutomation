"""FlowCut 动态上下文 Provider。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from simpleclaw.context.providers import (
    AttentionPacket,
    ContextBuildContext,
    ContextSection,
)

if TYPE_CHECKING:
    from Flowcut.storage.task_repo import RuntimeTaskRepository
    from Flowcut.storage.script_repo import ScriptRepository
    from Flowcut.storage.creative_repo import CreativeRepository


class TaskContextProvider:
    """每轮对话注入当前制作任务状态：任务 ID/步骤、选中脚本、素材匹配结果。

    数据来自 session 关联的最新 creative + script 记录。
    """

    def __init__(
        self,
        *,
        task_repo: "RuntimeTaskRepository",
        script_repo: "ScriptRepository | None" = None,
        creative_repo: "CreativeRepository | None" = None,
        source: str = "task_context",
    ) -> None:
        self._task_repo = task_repo
        self._script_repo = script_repo
        self._creative_repo = creative_repo
        self._source = source

    async def collect_dynamic_context(
        self,
        ctx: ContextBuildContext,
    ) -> list[ContextSection]:
        """返回当前任务状态的文本摘要，注入到 prompt 动态尾部。

        无活跃任务时返回空列表。
        TODO: 实现后填充真实任务状态。
        """
        return []


class UIContextAttentionProvider:
    """每轮注入用户当前界面位置（route / tab / drama）作为 attention packet。

    不持久化到对话历史；ContextBuilder 每轮重新注入当前状态。
    """

    def __init__(self) -> None:
        self._ui_context: dict | None = None

    def set_ui_context(self, ui_context: dict | None) -> None:
        self._ui_context = ui_context or None

    async def collect_attention(
        self,
        ctx: "ContextBuildContext",
    ) -> list[AttentionPacket]:
        del ctx
        if not self._ui_context:
            return []
        lines = ["[用户当前界面位置]"]
        if route := self._ui_context.get("route"):
            lines.append(f"route: {route}")
        if tab := self._ui_context.get("tab"):
            lines.append(f"tab: {tab}")
        if drama := self._ui_context.get("drama"):
            lines.append(f"drama: {drama}")
        if len(lines) == 1:
            return []
        return [
            AttentionPacket(
                content="\n".join(lines),
                source="ui_context",
                lifetime="always",
                placement="before_last_user",
                role="system",
            )
        ]
