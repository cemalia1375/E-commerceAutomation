"""ReactLoop 向调用方 yield 的事件类型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TextEvent:
    """来自 LLM 的流式文本 token。"""

    token: str


@dataclass
class ToolResultEvent:
    """工具执行的结果。"""

    tool_name: str
    result: str


@dataclass
class DoneEvent:
    """表示当前 ReAct 轮次已完成。"""


@dataclass
class ErrorEvent:
    """表示本轮次发生了不可恢复的错误。"""

    message: str


Event = TextEvent | ToolResultEvent | DoneEvent | ErrorEvent
