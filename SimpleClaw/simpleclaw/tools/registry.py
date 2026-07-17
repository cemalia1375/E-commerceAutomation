"""ToolRegistry —— Agent 的工具分发层。

注册表有三项职责：

  1. schemas()        —— 按 exposure 返回工具 JSON Schema，供 LLM 的 `tools` 参数使用
  2. execute(call)    —— 将 ToolCall 分发给对应工具并返回其 ToolResult
  3. needs_followup() —— 告知 Loop 某次调用是循环耦合还是循环解耦

ReactLoop 持有一个 ToolRegistry，从不直接操作单个 Tool 对象。

不传 ToolExposureState 时，schemas() 保持旧行为：注册即暴露。
"""

from __future__ import annotations

from simpleclaw.core.messages import ToolCall
from simpleclaw.harness.lifecycle import ToolLifecycle
from simpleclaw.tools.base import Tool, ToolResult
from simpleclaw.tools.catalog import ToolCatalog, ToolExposureState
from simpleclaw.tools.execution_runtime import ToolExecutionRuntime

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from simpleclaw.runtime.services import RuntimeServices
    from simpleclaw.tools.invocation import ToolInvocationStore


class ToolRegistry:
    """ReactLoop 会话中所有可用工具的注册表。"""

    def __init__(
        self,
        runtime_services: "RuntimeServices | None" = None,
        tool_lifecycle: ToolLifecycle | None = None,
        invocation_store: "ToolInvocationStore | None" = None,
        *,
        tool_catalog: ToolCatalog | None = None,
        exposure_state: ToolExposureState | None = None,
    ) -> None:
        self._catalog = tool_catalog or ToolCatalog()
        self._exposure_state = exposure_state
        self._execution_runtime = ToolExecutionRuntime(
            runtime_services=runtime_services,
            tool_lifecycle=tool_lifecycle,
            invocation_store=invocation_store,
        )

    @property
    def catalog(self) -> ToolCatalog:
        return self._catalog

    @property
    def exposure_state(self) -> ToolExposureState | None:
        return self._exposure_state

    def set_exposure_state(self, exposure_state: ToolExposureState | None) -> None:
        self._exposure_state = exposure_state

    @property
    def runtime_services(self) -> "RuntimeServices | None":
        return self._execution_runtime.runtime_services

    def set_runtime_services(self, runtime_services: "RuntimeServices | None") -> None:
        self._execution_runtime.set_runtime_services(runtime_services)

    @property
    def tool_lifecycle(self) -> ToolLifecycle | None:
        return self._execution_runtime.tool_lifecycle

    def set_tool_lifecycle(self, tool_lifecycle: ToolLifecycle | None) -> None:
        self._execution_runtime.set_tool_lifecycle(tool_lifecycle)

    @property
    def invocation_store(self) -> "ToolInvocationStore | None":
        return self._execution_runtime.invocation_store

    def set_invocation_store(self, invocation_store: "ToolInvocationStore | None") -> None:
        self._execution_runtime.set_invocation_store(invocation_store)

    @property
    def tool_names(self) -> list[str]:
        return self._catalog.tool_names

    @property
    def tools(self) -> list[Tool]:
        """Return registered tool instances.

        Callers may iterate this list to inject per-turn context. They should
        not mutate returned tools unless they own this registry for a single
        session/agent lane.
        """
        return self._catalog.tools

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------

    def register(self, tool: Tool) -> None:
        """添加工具。若同名工具已注册则抛出 ValueError。"""
        self._catalog.register(tool)

    # ------------------------------------------------------------------
    # Schema 导出（供 LLM 使用）
    # ------------------------------------------------------------------

    def schemas(self, exposure: ToolExposureState | Iterable[str] | None = None) -> list[dict]:
        """返回当前可见工具的 OpenAI 格式 Schema 列表。

        该列表直接传递给 LLMProvider.stream(tools=...)。

        若未提供 exposure，且 registry 没有绑定 exposure_state，则保持旧行为：
        返回所有已注册工具。
        """
        return self._catalog.schemas_for(self.visible_tool_names(exposure))

    def visible_tool_names(self, exposure: ToolExposureState | Iterable[str] | None = None) -> list[str]:
        """Return model-visible tool names for a given exposure state."""
        effective = exposure if exposure is not None else self._exposure_state
        if effective is None:
            return self.tool_names
        if isinstance(effective, ToolExposureState):
            return effective.visible_tool_names(self._catalog)
        allowed = set(str(name).strip() for name in effective if str(name).strip())
        return [name for name in self.tool_names if name in allowed]

    # ------------------------------------------------------------------
    # 分发
    # ------------------------------------------------------------------

    async def execute(self, call: ToolCall) -> ToolResult:
        """将 ToolCall 分发给对应工具并返回其结果。

        若工具名未知，返回 ToolResult(ok=False) 而非抛出异常 ——
        Loop 随后可将错误信息作为工具结果注入到对话中。
        """
        tool = self._catalog.get(call.name)
        return await self._execution_runtime.invoke(call, tool, self.tool_names)

    # ------------------------------------------------------------------
    # 循环耦合查询
    # ------------------------------------------------------------------

    def needs_followup(self, call: ToolCall) -> bool:
        """若此次调用的结果必须反馈给 LLM 则返回 True。

        对于未知工具默认返回 True（安全起见：将其视为循环耦合，
        以便错误信息能注入到对话中）。
        """
        tool = self._catalog.get(call.name)
        return tool.needs_followup if tool is not None else True
