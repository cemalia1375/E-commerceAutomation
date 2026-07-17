"""PostTurnEffects / SubmittedTask — 子 Agent 副作用调度的占位类型。

在 nanobot 原始架构里，PostTurnEffects 聚合 postprocess / structured_memory /
skin_profile_sync 等多条副作用管道。SimpleClaw 当前子 Agent 并未启用这条路径
（SubagentBase.make_post_turn_effects 默认返回 None，SkinDiarySubagent 也没
覆盖），所以这里只留最小占位，保证 import 和类型标注不报错。

未来如果要启用，可以在子 Agent 类里实例化 PostTurnEffects 并实现 dispatch。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simpleclaw.harness.hooks import TurnContext


@dataclass
class SubmittedTask:
    """一次副作用任务入队的结果。"""
    ok: bool
    task_type: str
    stream: str
    queue_id: str | None = None
    error: str | None = None


class PostTurnEffects:
    """副作用调度占位类。

    子 Agent 目前不使用；保留是为了让 SubagentBase / SkinDiarySubagent 的类型
    引用不报错。真实实现加进来时，只需重写 dispatch() 返回 list[SubmittedTask]。
    """

    async def dispatch(self, ctx: "TurnContext") -> list[SubmittedTask]:
        return []
