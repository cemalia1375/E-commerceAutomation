"""MainAgent — FlowCut 主 Agent 的装配层。

SessionStore 冷启动时调用：
  - make_context_builder(tenant_key) → ContextBuilder
  - make_tool_registry(tenant_key)   → ToolRegistry
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from simpleclaw.context.builder import ContextBuilder
from simpleclaw.context.providers import ContextBuildContext, ContextSection
from simpleclaw.harness.lifecycle import ToolLifecycle
from simpleclaw.skills import SkillRegistry
from simpleclaw.tools.base import Tool
from simpleclaw.tools.builtin.skill import LoadSkillTool, ReadSkillAssetTool, UnloadSkillTool
from simpleclaw.tools.registry import ToolRegistry

from Flowcut.agent.capabilities import AgentCapabilities
from Flowcut.config import load_stable_sections
from Flowcut.context.providers import TaskContextProvider, UIContextAttentionProvider

if TYPE_CHECKING:
    from Flowcut.storage.database import Database
    from Flowcut.storage.task_repo import RuntimeTaskRepository
    from Flowcut.storage.material_repo import MaterialRepository
    from Flowcut.storage.script_repo import ScriptRepository
    from Flowcut.storage.creative_repo import CreativeRepository


@dataclass(slots=True)
class _CurrentTimeContextProvider:
    """向 prompt 动态尾部注入北京时间。"""

    source: str = "current_time"

    async def collect_dynamic_context(
        self,
        ctx: ContextBuildContext,
    ) -> list[ContextSection]:
        del ctx
        now_cn = datetime.now(timezone(timedelta(hours=8)))
        weekday_map = {
            "Monday": "周一", "Tuesday": "周二", "Wednesday": "周三",
            "Thursday": "周四", "Friday": "周五", "Saturday": "周六", "Sunday": "周日",
        }
        weekday_cn = weekday_map.get(now_cn.strftime("%A"), now_cn.strftime("%A"))
        time_str = now_cn.strftime("%Y-%m-%d %H:%M:%S")
        note = (
            "当前时间（北京，UTC+8）："
            + time_str + " " + weekday_cn
            + "。涉及提醒、定时任务或“多久之后”的请求时，必须基于这个时间计算未来时间。"
        )
        return [ContextSection(content=note, source=self.source)]


class MainAgent:
    """FlowCut 主 Agent 的装配层。

    FlowCut 暂无 journey 阶段，stable_cache 仅预热 "default" 一个 stage。
    """

    _VALID_STAGES: tuple[str, ...] = ("default",)

    def __init__(
        self,
        *,
        db: "Database",
        task_repo: "RuntimeTaskRepository",
        material_repo: "MaterialRepository",
        base_registry: ToolRegistry,
        tool_factories: list[Callable[[str], Tool]] | None = None,
        script_repo: "ScriptRepository | None" = None,
        creative_repo: "CreativeRepository | None" = None,
    ) -> None:
        self._db = db
        self._task_repo = task_repo
        self._material_repo = material_repo
        self._base_registry = base_registry
        self._tool_factories = tool_factories or []
        self._script_repo = script_repo
        self._creative_repo = creative_repo

        # 启动时预热稳定段落（文件 I/O 只发生一次）
        self._stable_cache: dict[str, list[str]] = {
            "default": load_stable_sections(),
        }
        self._skill_registry = SkillRegistry(
            roots=[Path(__file__).resolve().parents[1] / "skills"],
        )

    # ------------------------------------------------------------------
    # 冷启动装配（SessionStore.get_or_create 调用）
    # ------------------------------------------------------------------

    async def make_context_builder(
        self,
        tenant_key: str,
        stage: str = "default",
        capabilities: AgentCapabilities | None = None,
    ) -> ContextBuilder:
        """为指定租户创建 ContextBuilder。"""
        del capabilities
        stable = self._stable_cache.get(stage) or self._stable_cache["default"]
        ui_ctx_provider = UIContextAttentionProvider()
        return ContextBuilder(
            stable_sections=stable,
            stable_prompt_providers=[],
            dynamic_context_providers=self._make_dynamic_context_providers(tenant_key),
            attention_providers=[ui_ctx_provider],
            skill_registry=self._skill_registry,
            include_skill_index=True,
            tenant_key=tenant_key,
            cache_lane="main_agent",
        )

    def _make_dynamic_context_providers(self, tenant_key: str) -> list:
        del tenant_key
        return [
            TaskContextProvider(
                task_repo=self._task_repo,
                script_repo=self._script_repo,
                creative_repo=self._creative_repo,
                source="task_context",
            ),
            _CurrentTimeContextProvider(source="current_time"),
        ]

    def make_tool_registry(
        self,
        tenant_key: str,
        stage: str = "default",
        capabilities: AgentCapabilities | None = None,
    ) -> ToolRegistry:
        """为指定租户组装 ToolRegistry。"""
        del stage, capabilities
        reg = ToolRegistry(
            runtime_services=self._base_registry.runtime_services,
            tool_lifecycle=self._make_tool_lifecycle(),
        )
        for tool in self._base_registry.tools:
            if hasattr(tool, "set_context"):
                raise ValueError(
                    "Contextual base tool must be registered via tool_factories"
                )
            reg.register(tool)
        reg.register(LoadSkillTool())
        reg.register(UnloadSkillTool())
        reg.register(ReadSkillAssetTool())
        for factory in self._tool_factories:
            reg.register(factory(tenant_key))
        return reg

    def _make_tool_lifecycle(self) -> ToolLifecycle:
        return ToolLifecycle(before_tool_hooks=[])
