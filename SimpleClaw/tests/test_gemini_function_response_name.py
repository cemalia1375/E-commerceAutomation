"""GeminiLLM._convert_messages: 确保 tool 消息回传时 FunctionResponse.name
等于上一轮 FunctionCall.name（而不是兜底 "tool"）。

修复前的 bug：tool 消息序列化后只带 tool_call_id / content，没有 name 字段；
GeminiLLM 转换时 fallback 到 "tool"，导致 Gemini 校验失败：400 INVALID_ARGUMENT。
触发场景：用户上传爆款视频后，agent 调用任意 durable / inline 工具（如
check_task_status、decompose_video），下一轮 LLM 调用就会爆 400。
"""

import pytest
from google.genai import types

from simpleclaw.llm.gemini import GeminiLLM


@pytest.mark.unit
def test_tool_message_preserves_function_call_name():
    """tool 角色 dict 只有 tool_call_id，没有 name 字段时，
    GeminiLLM._convert_messages 应根据 tool_call_id 反查上一轮 assistant
    的 tool_calls，把真名填进 FunctionResponse.name。
    """
    messages = [
        {"role": "user", "content": "请检查任务 abc 状态"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "check_task_status",
                        "arguments": '{"task_id": "abc"}',
                    },
                    "thought_signature": b"\x01\x02",
                }
            ],
            "pending_signatures": [],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "任务 abc 不存在",
        },
    ]

    _, contents = GeminiLLM._convert_messages(messages)

    function_response_parts = [
        part
        for content in contents
        for part in content.parts
        if getattr(part, "function_response", None) is not None
    ]
    assert len(function_response_parts) == 1
    fr = function_response_parts[0].function_response
    assert fr.name == "check_task_status", (
        f"FunctionResponse.name 应为上一轮 FunctionCall.name "
        f"check_task_status，实际为 {fr.name!r}（兜底 'tool' 会让 Gemini 返回 400 INVALID_ARGUMENT）"
    )
    assert fr.id == "call_1"
    assert fr.response == {"output": "任务 abc 不存在"}


@pytest.mark.unit
def test_multiple_tool_calls_each_get_correct_name():
    """同一 assistant 回合并发调多个工具时，每个 tool 结果都应正确反查到自己的名字。"""
    messages = [
        {"role": "user", "content": "并发查两个任务"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_a",
                    "type": "function",
                    "function": {"name": "check_task_status", "arguments": "{}"},
                    "thought_signature": b"\x01",
                },
                {
                    "id": "call_b",
                    "type": "function",
                    "function": {"name": "search_materials", "arguments": "{}"},
                    "thought_signature": b"\x02",
                },
            ],
            "pending_signatures": [],
        },
        {"role": "tool", "tool_call_id": "call_a", "content": "A"},
        {"role": "tool", "tool_call_id": "call_b", "content": "B"},
    ]

    _, contents = GeminiLLM._convert_messages(messages)

    by_id = {
        part.function_response.id: part.function_response.name
        for content in contents
        for part in content.parts
        if getattr(part, "function_response", None) is not None
    }
    assert by_id == {"call_a": "check_task_status", "call_b": "search_materials"}


@pytest.mark.unit
def test_explicit_name_field_takes_precedence():
    """若 tool 消息字典显式带了 name 字段（向后兼容），优先使用它。"""
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_x",
                    "type": "function",
                    "function": {"name": "fallback_name", "arguments": "{}"},
                    "thought_signature": b"\x00",
                }
            ],
            "pending_signatures": [],
        },
        {
            "role": "tool",
            "tool_call_id": "call_x",
            "name": "explicit_name",
            "content": "ok",
        },
    ]

    _, contents = GeminiLLM._convert_messages(messages)
    fr_parts = [
        p for c in contents for p in c.parts if getattr(p, "function_response", None)
    ]
    assert fr_parts[0].function_response.name == "explicit_name"


@pytest.mark.unit
def test_unknown_tool_call_id_falls_back_safely():
    """tool_call_id 在当前消息窗口里查不到对应 tool_call 时（历史被裁剪过），
    退化为 'unknown_tool' 而不是 'tool'。Gemini 仍可能拒绝，但至少不会假装
    没事；同时不影响其他可正确反查的消息。
    """
    messages = [
        {"role": "tool", "tool_call_id": "orphan", "content": "孤儿结果"},
    ]
    _, contents = GeminiLLM._convert_messages(messages)
    fr_parts = [
        p for c in contents for p in c.parts if getattr(p, "function_response", None)
    ]
    assert len(fr_parts) == 1
    assert fr_parts[0].function_response.name == "unknown_tool"
