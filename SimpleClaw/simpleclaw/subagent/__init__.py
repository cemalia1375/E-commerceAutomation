"""SimpleClaw 子 Agent 框架层。

SubagentBase  — 子 Agent 类型的抽象接口
SubagentRunner — 无状态执行器（包装 ReactLoop，持久化由调用方负责）
"""

from simpleclaw.subagent.base import SubagentBase
from simpleclaw.subagent.runner import SubagentRunner
from simpleclaw.subagent.runtime import (
    SubagentArtifact,
    SubagentArtifactStatus,
    SubagentPermission,
    SubagentRunMode,
    SubagentRunOwnerType,
    SubagentRunRequest,
    SubagentRunResult,
    SubagentRunStatus,
    subagent_run_scope_key,
)

__all__ = [
    "SubagentBase",
    "SubagentRunner",
    "SubagentArtifact",
    "SubagentArtifactStatus",
    "SubagentPermission",
    "SubagentRunMode",
    "SubagentRunOwnerType",
    "SubagentRunRequest",
    "SubagentRunResult",
    "SubagentRunStatus",
    "subagent_run_scope_key",
]
