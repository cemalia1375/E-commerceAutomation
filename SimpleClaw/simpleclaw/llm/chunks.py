"""LLMProvider.stream() 所 yield 的规范化 chunk 类型。

原始 LLM 输出因调用模式而异：
  FC 模式    ：API 原生结构化块（text_delta / tool_use）
  ReAct 模式：工具调用嵌入在纯文本中，由提供方解析

两种模式在此统一规范化为 TextChunk / ToolCallChunk，
使得 ReactLoop 无需关心当前使用的是哪种模式。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TextChunk:
    """来自 LLM 的流式文本增量。

    thought_signature: Gemini 私有字段（bytes，不透明）。text Part 上的签名，
    建议回传给 Gemini 以保持推理质量（非强制，省略不会触发 400，但可能降低性能）。
    """

    token: str
    thought_signature: bytes | None = None


@dataclass
class ToolCallChunk:
    """完整且已完全组装的工具调用块。

    提供方负责将增量的 input_json_delta 片段累积完整，
    再 yield 此 chunk。

    thought_signature: Gemini 私有字段（bytes，不透明）。后续轮次把同一
    function_call 回传给 Gemini 时必须原样附带，否则会触发
    INVALID_ARGUMENT(400) "Function call is missing a thought_signature"。
    其它 provider 无此概念，保持 None 即可。
    """

    id: str
    name: str
    arguments: dict
    thought_signature: bytes | None = None


Chunk = TextChunk | ToolCallChunk
