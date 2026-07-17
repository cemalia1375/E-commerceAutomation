"""SubagentBase — 持久化子 Agent 类型的抽象接口。

每种子 Agent 类型（如肌肤日记）继承此类，声明：
  - 如何根据 tenant_key 派生 session_key
  - 如何识别某个 session_key 属于本类型（用于路由）
  - 如何为某个租户构建 ContextBuilder（可读 DB）
  - 如何为某个租户构建 ToolRegistry

SubagentRunner 只依赖这些接口，不关心具体业务。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from simpleclaw.context.builder import ContextBuilder
from simpleclaw.context.providers import (
    AttentionPacket,
    AttentionProvider,
    ContextSection,
    DynamicContextProvider,
    StablePromptProvider,
)
from simpleclaw.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from simpleclaw.harness.hooks import PostrunHook
    from simpleclaw.harness.hooks import TurnContext
    from simpleclaw.runtime.side_effects import PostTurnEffects


class SubagentBase(ABC):
    """持久化子 Agent 类型的抽象基类。

    子类在 Mojing 业务层实现，SimpleClaw 框架层只用接口。

    必须实现的抽象方法（5个）：name / session_key_for / matches /
    make_context_builder / make_tool_registry。

    可选覆盖的方法：fetch_dynamic_context_sections / fetch_attention_packets /
    prepare_turn_media / make_postprocess_hook / make_cold_path_hook /
    make_post_turn_effects / on_turn_completed。
    默认实现均为空操作，子 Agent 按需覆盖以获得完整的"mini 主 Agent"能力。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """子 Agent 类型的唯一名称，如 'skin_diary'。"""
        ...

    @abstractmethod
    def session_key_for(self, tenant_key: str) -> str:
        """根据 tenant_key 派生该子 Agent 的 session_key。

        例：skin_diary:{tenant_key}
        """
        ...

    @abstractmethod
    def matches(self, session_key: str) -> bool:
        """判断 session_key 是否属于本子 Agent 类型（用于路由判断）。

        例：return session_key.startswith("skin_diary:")
        """
        ...

    @abstractmethod
    async def make_context_builder(self, tenant_key: str) -> ContextBuilder:
        """为指定租户创建 ContextBuilder（稳定前缀，适合 Anthropic cache）。

        只放真正稳定的内容（子 Agent prompt、工具产出快照等），
        每轮变化的内容（USER.md 等）通过 fetch_dynamic_context_sections() 注入。
        """
        ...

    def make_stable_prompt_providers(
        self,
        tenant_key: str,
    ) -> list[StablePromptProvider]:
        """返回通用稳定提示词 provider。

        SimpleClaw 只定义 provider 协议；业务层可按场景实现并在
        make_context_builder() 中传给 ContextBuilder。
        """
        del tenant_key
        return []

    def make_dynamic_context_providers(
        self,
        tenant_key: str,
    ) -> list[DynamicContextProvider]:
        """返回通用动态上下文 provider。"""
        del tenant_key
        return []

    def make_attention_providers(
        self,
        tenant_key: str,
    ) -> list[AttentionProvider]:
        """返回通用 attention provider。"""
        del tenant_key
        return []

    @abstractmethod
    def make_tool_registry(self, tenant_key: str) -> ToolRegistry:
        """为指定租户创建 ToolRegistry（仅包含子 Agent 可用的工具）。"""
        ...

    # ------------------------------------------------------------------
    # 可选方法：默认为空操作，子 Agent 按需覆盖
    # ------------------------------------------------------------------

    async def fetch_dynamic_context_sections(
        self,
        tenant_key: str,
        *,
        message: str = "",
        media: list[str] | None = None,
        report_id: str | None = None,
    ) -> list[ContextSection]:
        """返回本轮动态上下文段落。

        这是 SimpleClaw 的通用动态上下文入口。业务层可基于当前
        message/media/report_id 注入画像、运行状态、按需技能或图片复核提示；
        返回值必须是结构化 ContextSection，而不是散装 prompt 字符串。
        """
        del tenant_key, message, media, report_id
        return []

    async def prepare_turn_media(
        self,
        tenant_key: str,
        *,
        message: str = "",
        media: list[str] | None = None,
    ) -> list[str]:
        """返回本轮实际送入多模态模型的图片列表。

        默认只使用用户本轮上传的 media。子类可覆盖，在明确需要复核历史图时
        追加最近图片。注意：这不代表用户本轮上传了新图；cold_path 仍应使用
        原始 media 判断上传信号。
        """
        del tenant_key, message
        return list(media or [])

    async def fetch_attention_packets(
        self,
        tenant_key: str,
        *,
        message: str = "",
        media: list[str] | None = None,
        report_id: str | None = None,
    ) -> list[AttentionPacket]:
        """返回本轮结构化注意力包。

        reminder、临时用户补充、按需技能提示、执行规范提醒都应进入这里，
        由 ContextBuilder 统一排序和放置。
        """
        del tenant_key, message, media, report_id
        return []

    def make_postprocess_hook(self) -> "PostrunHook | None":
        """返回子 Agent 专用的 PostprocessHook 实例（单例，在 __init__ 创建）。

        默认返回 None（不运行 postprocess）。
        """
        return None

    def make_cold_path_hook(self) -> "PostrunHook | None":
        """返回子 Agent 专用的 ColdPathHook 实例（单例，在 __init__ 创建）。

        默认返回 None（不运行冷路径）。
        """
        return None

    def make_post_turn_effects(self) -> "PostTurnEffects | None":
        """返回子 Agent 专用的 PostTurnEffects 实例（单例，外部注入）。

        若返回非 None，SubagentStore 会通过它把 post-turn side effects
        以可靠、可观测的方式入队到 runtime；否则退回到旧的进程内 fire-and-forget。

        默认返回 None（不调度 post-turn side effects）。
        """
        return None

    async def on_turn_completed(self, ctx: "TurnContext") -> None:
        """同步的子 Agent 轮次完成回调。

        调用点在会话锁内、post-turn 后台任务入队前，适合更新下一轮必须立即
        可见的小型业务状态（例如检测异议状态机）。默认无操作。
        """
        del ctx
        return None

    def memory_source(self) -> str:
        """返回该子 Agent 写入长期记忆时使用的 source 名称。

        默认与子 Agent 的 name 一致；若未来需要与显示名分离，可在子类覆盖。
        """
        return self.name
