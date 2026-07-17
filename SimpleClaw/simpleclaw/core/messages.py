"""Agent 对话历史的核心消息类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """LLM 请求的单次工具调用。

    thought_signature 仅 Gemini 使用，bytes，不透明。下一轮把同一调用回传
    时必须原样附带；其它 provider 保持 None。
    """

    id: str
    name: str
    arguments: dict
    thought_signature: bytes | None = None


@dataclass
class UserMessage:
    """来自用户的消息。"""

    content: str | list[dict[str, Any]]


@dataclass
class AssistantMessage:
    """来自助手的消息，可选地包含工具调用。

    pending_signatures: 本 assistant 回合内 text Part / thought-only Part 上
    累积的 thought_signature（不绑给具体 tool_call）。回放时作为前置 thought
    Parts 发回给 Gemini 以保持推理质量。
    """

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    pending_signatures: list[bytes] = field(default_factory=list)


@dataclass
class ToolResultMessage:
    """工具调用的返回结果，以原始调用 ID 作为键。"""

    call_id: str
    content: str


Message = UserMessage | AssistantMessage | ToolResultMessage
