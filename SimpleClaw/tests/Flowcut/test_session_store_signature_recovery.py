"""测试 _deserialize_messages 对旧版无 thought_signature 历史的 dummy 签名兜底逻辑。

任务 A 验收测试：
  用例 1: 旧脏数据（assistant.tool_calls 无 b64）→ 签名等于 dummy，后续消息全保留
  用例 2: 新干净数据（有合法 b64）→ 签名等于解码后的 bytes，不受影响
  用例 3: 混合数据（前半旧无 sig + 后半新有 sig）→ 全部保留，各自签名正确
"""

import base64

import pytest

from Flowcut.storage.session_store import _DUMMY_SIGNATURE, _deserialize_messages
from simpleclaw.core.messages import AssistantMessage, ToolResultMessage, UserMessage


@pytest.mark.unit
def test_old_dirty_data_gets_dummy_signature_and_history_preserved():
    """旧脏数据（tool_calls 无 thought_signature_b64）→
    签名等于 dummy，且后续所有 messages 都保留（不截断）。
    """
    openai_msgs = [
        {"role": "user", "content": "你好"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_001",
                    "type": "function",
                    "function": {"name": "search", "arguments": {"q": "test"}},
                    # 刻意不包含 thought_signature_b64
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_001", "content": "结果A"},
        {"role": "user", "content": "继续"},
    ]
    messages = _deserialize_messages(openai_msgs)

    # 全部 4 条消息都保留，不被截断
    assert len(messages) == 4

    user0, assistant1, tool2, user3 = messages
    assert isinstance(user0, UserMessage)
    assert isinstance(assistant1, AssistantMessage)
    assert isinstance(tool2, ToolResultMessage)
    assert isinstance(user3, UserMessage)

    # tool_call 上使用 dummy signature
    assert len(assistant1.tool_calls) == 1
    assert assistant1.tool_calls[0].thought_signature == _DUMMY_SIGNATURE


@pytest.mark.unit
def test_clean_data_with_valid_b64_decoded_correctly():
    """新干净数据（有合法 b64）→ 签名等于解码后的 bytes，不受影响。"""
    original_sig = b"\x01\x02\x03\xAB\xCD"
    sig_b64 = base64.b64encode(original_sig).decode("ascii")

    openai_msgs = [
        {"role": "user", "content": "请求"},
        {
            "role": "assistant",
            "content": "好的",
            "tool_calls": [
                {
                    "id": "call_100",
                    "type": "function",
                    "function": {"name": "do_something", "arguments": {}},
                    "thought_signature_b64": sig_b64,
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_100", "content": "done"},
    ]
    messages = _deserialize_messages(openai_msgs)

    assert len(messages) == 3
    assistant = messages[1]
    assert isinstance(assistant, AssistantMessage)
    assert len(assistant.tool_calls) == 1
    # 签名正确还原为原始 bytes，不是 dummy
    assert assistant.tool_calls[0].thought_signature == original_sig
    assert assistant.tool_calls[0].thought_signature != _DUMMY_SIGNATURE


@pytest.mark.unit
def test_mixed_data_all_preserved_with_correct_signatures():
    """混合数据（前半旧无 sig + 后半新有 sig）→
    全部保留，旧的用 dummy，新的用解码后 bytes。
    """
    real_sig = b"\xFF\xFE\x00\x01"
    sig_b64 = base64.b64encode(real_sig).decode("ascii")

    openai_msgs = [
        {"role": "user", "content": "第一轮"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "old_call",
                    "type": "function",
                    "function": {"name": "old_tool", "arguments": {}},
                    # 旧数据无 thought_signature_b64
                }
            ],
        },
        {"role": "tool", "tool_call_id": "old_call", "content": "旧结果"},
        {"role": "user", "content": "第二轮"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "new_call",
                    "type": "function",
                    "function": {"name": "new_tool", "arguments": {}},
                    "thought_signature_b64": sig_b64,
                }
            ],
        },
        {"role": "tool", "tool_call_id": "new_call", "content": "新结果"},
    ]
    messages = _deserialize_messages(openai_msgs)

    # 6 条全保留
    assert len(messages) == 6

    old_assistant = messages[1]
    new_assistant = messages[4]

    assert isinstance(old_assistant, AssistantMessage)
    assert isinstance(new_assistant, AssistantMessage)

    # 旧条目用 dummy，新条目用真实 bytes
    assert old_assistant.tool_calls[0].thought_signature == _DUMMY_SIGNATURE
    assert new_assistant.tool_calls[0].thought_signature == real_sig
