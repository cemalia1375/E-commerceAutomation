"""工具基类与结果类型。

Agent 能够调用的每个工具都必须继承 Tool。

execution_mode 标志
-------------------
这是工具执行位置标志：

  inline（默认）→ 调用 tool.execute()，结果直接返回给 ReAct loop。

  durable → 调用 tool.prepare_task() 获取 TaskEnvelope，
      ToolExecutionRuntime 统一 submit_task 后返回标准 ack。
      适用于「触发后台工作 + 立即回应」的工具，例如深度报告、图片分析。

execution_mode 与 needs_followup 是正交两轴：

  inline  + needs_followup=True   → 经典同步工具（search、calculator）
  inline  + needs_followup=False  → fire-and-forget 副作用
  durable + needs_followup=True   → 触发后台任务后，让 LLM 在下一轮自然确认
  durable + needs_followup=False  → 纯派发，不进入下一轮 LLM

needs_followup 标志
-------------------
这是循环耦合标志：

  needs_followup=True（默认）→ 循环耦合
      工具结果必须在本轮结束前反馈给 LLM。
      ReactLoop 会收集所有耦合结果，并以它们注入消息历史的方式
      发起新一轮 LLM 迭代。
      示例：search、calculator —— 答案会影响最终回复。

  needs_followup=False → 循环解耦
      工具是纯副作用操作，其结果无需返回给 LLM；
      ReactLoop 将其作为后台任务触发后继续执行。
      示例：send_notification、log_event、trigger_async_pipeline。
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from simpleclaw.runtime.task_protocol import TaskEnvelope


ToolExecutionMode = Literal["inline", "durable"]
ToolRiskLevel = Literal["low", "medium", "high"]
ToolExposureScope = Literal["global", "tenant", "session", "agent", "skill"]


@dataclass
class ToolResult:
    """单次工具执行的结果。

    当 content 是 JSON object 时，ToolExecutionRuntime 会读取 ok / action /
    status / error / created_new_job / deduped 等字段进行结果归一化。
    其中 submitted、queued、wait_external、accepted、deduped、blocked/deferred 等 action/status
    会被视为成功；legacy noop 会归一到 succeeded；ok=False 或 error 会被视为模型可见失败。
    """

    content: str          # LLM 将看到的文本（当 needs_followup=True 时）
    ok: bool = True       # False 表示工具级错误；Loop 仍会将 content 注入对话
    persist_to_history: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


class Tool(ABC):
    """所有 Agent 工具的抽象基类。"""

    # --- 标识信息 ---
    name: str
    description: str
    parameters: dict      # 描述工具输入的 JSON Schema 对象

    # --- 循环行为 ---
    needs_followup: bool = True
    execution_mode: ToolExecutionMode = "inline"
    durable_action: str = "queued"
    tool_category: str = "sync_read"
    business_ref_type: str | None = None
    business_ref_id_field: str | None = None

    # --- 可见性与治理元数据 ---
    # 默认保持旧行为：未标记 deferred 的工具会暴露。业务或 MCP 重工具可显式 deferred。
    always_load: bool = False
    should_defer: bool = False
    search_hint: str = ""
    risk_level: ToolRiskLevel = "low"
    read_only: bool = False
    destructive: bool = False
    concurrency_safe: bool = True
    requires_approval: bool = False
    exposure_scope: ToolExposureScope = "session"

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """规范化 LLM 传入的参数；默认不修改。"""
        return params

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        """返回参数错误列表；默认不校验。"""
        return []

    async def execute(self, **kwargs) -> ToolResult:
        """使用 LLM 提供的参数运行 inline 工具。

        kwargs 的键与 self.parameters 中声明的属性对应。
        始终返回 ToolResult —— 不要从此处抛出异常；
        请将错误包装为 ToolResult(ok=False, content=<错误信息>) 的形式返回。
        """
        raise NotImplementedError(f"Tool '{self.name}' does not implement execute()")

    async def prepare_task(self, **kwargs) -> "TaskEnvelope | ToolResult":
        """为 durable 工具准备任务；可返回 ToolResult 进行短路。"""
        raise NotImplementedError(f"Tool '{self.name}' does not implement prepare_task()")

    async def on_task_submitted(self, task: "TaskEnvelope", queue_id: str) -> None:
        """durable task 成功入队后的回调；默认无操作。"""
        del task, queue_id

    def durable_result(self, task: "TaskEnvelope", queue_id: str) -> ToolResult:
        """durable task 成功入队后返回给 ReAct loop 的标准结果。"""
        import json

        return ToolResult(
            content=json.dumps(
                {
                    "ok": True,
                    "action": self.durable_action,
                    "task_id": task.task_id,
                    "task_type": task.task_type,
                    "trace_id": task.trace_id,
                    "queue_id": queue_id,
                },
                ensure_ascii=False,
            )
        )
