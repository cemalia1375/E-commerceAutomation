"""PostrunHook — 每轮 Agent 执行结束后运行后处理工作的接口。

Hook 只定义业务回调契约；调用方可以选择直接 await、提交 durable task queue，
或在自己的生命周期管理中创建后台任务。

用法
----
    class MyHook(PostrunHook):
        async def on_turn_end(self, ctx: TurnContext) -> None:
            # 使用 ctx.user_message / ctx.assistant_reply 执行工作
            ...

    # 在服务启动时：
    hooks = [MyHook(...)]

    # 每轮结束后（示例：同步等待 hook 完成）：
    for hook in hooks:
        await hook.on_turn_end(ctx)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TurnContext:
    """轮次完成后传递给每个 PostrunHook 的数据。"""

    tenant_key: str       # 标识用户 / 租户
    session_key: str      # 标识会话
    user_message: str     # 本轮用户的输入
    assistant_reply: str  # 助手累积的文本响应
    media: list[str] = field(default_factory=list)  # 本轮用户上传的图片 URL（可空）
    first_token_reply: str = ""  # first_token_llm 输出的短开场（可空）
    main_assistant_reply: str = ""  # 正式 Agent 输出，不含 first_token_reply（可空）
    postprocess_hints: list[dict] = field(default_factory=list)  # 工具给后台沉淀的结构化提示
    tool_calls: list[dict] = field(default_factory=list)  # 本轮消息历史中的工具调用事实
    tool_results: list[dict] = field(default_factory=list)  # 本轮工具结果事实
    tool_invocations: list[dict] = field(default_factory=list)  # 工具调用落库事实
    runtime_tasks: list[dict] = field(default_factory=list)  # 本轮 runtime task 状态事实


class PostrunHook(ABC):
    """每轮完成后执行后台任务的接口。"""

    @abstractmethod
    async def on_turn_end(self, ctx: TurnContext) -> None:
        """为刚完成的轮次执行后台工作。

        契约：
          - 不得抛出异常。将所有逻辑包裹在 try/except 中并记录失败信息。
          - 不得写入 SSE 流。
          - 可以调用 LLM、写入数据库、入队任务等。
        """
        ...
