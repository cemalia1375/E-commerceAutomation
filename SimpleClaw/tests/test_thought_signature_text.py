"""测试 text Part 上的 thought_signature round-trip（任务 B 验收测试）。

用例 1: TextChunk dataclass 能携带 thought_signature
用例 2: AssistantMessage 能携带 pending_signatures
用例 3: ContextBuilder.build() 输出的 assistant dict 包含 pending_signatures 字段
用例 4: GeminiLLM._convert_messages 把 pending_signatures 转成前置 thought Parts
用例 5(round-trip): AssistantMessage → _serialize_messages → _deserialize_messages → pending_signatures 恢复
用例 6(external): 构造 2 轮纯文本对话，把第 1 轮 text Part sig 通过 pending_signatures 回传，验证不报错
"""

import asyncio
import base64
import os

# asyncio.run() 在整个 test suite 中创建新 event loop，规避 integration 测试
# 遗留的 closed event loop 问题（asyncio.get_event_loop() 在这种场景会失败）

import pytest
from google.genai import types

from simpleclaw.llm.chunks import TextChunk
from simpleclaw.core.messages import AssistantMessage, UserMessage
from simpleclaw.llm.gemini import GeminiLLM
from simpleclaw.llm.config import GeminiConfig
from simpleclaw.context.builder import ContextBuilder
from Flowcut.storage.session_store import _serialize_messages, _deserialize_messages


# -----------------------------------------------------------------------
# 用例 1: TextChunk 能携带 thought_signature
# -----------------------------------------------------------------------

@pytest.mark.unit
def test_text_chunk_carries_thought_signature():
    """TextChunk dataclass 默认 thought_signature=None，可以显式传 bytes。"""
    chunk_no_sig = TextChunk(token="hello")
    assert chunk_no_sig.thought_signature is None

    sig = b"\xAA\xBB\xCC"
    chunk_with_sig = TextChunk(token="world", thought_signature=sig)
    assert chunk_with_sig.thought_signature == sig


# -----------------------------------------------------------------------
# 用例 2: AssistantMessage 能携带 pending_signatures
# -----------------------------------------------------------------------

@pytest.mark.unit
def test_assistant_message_carries_pending_signatures():
    """AssistantMessage 默认 pending_signatures=[]，可以传入 list[bytes]。"""
    msg_empty = AssistantMessage(content="hi")
    assert msg_empty.pending_signatures == []

    sigs = [b"\x01", b"\x02\x03"]
    msg_with_sigs = AssistantMessage(content="hi", pending_signatures=sigs)
    assert msg_with_sigs.pending_signatures == sigs


# -----------------------------------------------------------------------
# 用例 3: ContextBuilder.build() 输出的 assistant dict 含 pending_signatures
# -----------------------------------------------------------------------

@pytest.mark.unit
def test_context_builder_includes_pending_signatures_in_output():
    """有 pending_signatures 的 AssistantMessage 经 ContextBuilder.build()
    序列化后，输出 dict 中 pending_signatures 字段等于原始 list[bytes]。
    """
    sig = b"\xDE\xAD\xBE\xEF"
    history = [
        UserMessage(content="你好"),
        AssistantMessage(content="我来回答", pending_signatures=[sig]),
        UserMessage(content="继续"),
    ]
    builder = ContextBuilder(stable_sections=["# System"])
    result = asyncio.run(builder.build(history, query="继续"))

    # 找到 assistant role 的 dict
    assistant_dicts = [m for m in result if m.get("role") == "assistant"]
    assert len(assistant_dicts) == 1
    assert assistant_dicts[0]["pending_signatures"] == [sig]


# -----------------------------------------------------------------------
# 用例 4: GeminiLLM._convert_messages 把 pending_signatures 转成前置 thought Parts
# -----------------------------------------------------------------------

@pytest.mark.unit
def test_convert_messages_inserts_thought_parts_from_pending_signatures():
    """pending_signatures 中的每个 bytes 会在 assistant Content.parts 最前面
    插入一个只含 thought_signature 的 types.Part。
    """
    sig1 = b"\x01\x02"
    sig2 = b"\x03\x04"
    messages = [
        {
            "role": "assistant",
            "content": "好的",
            "pending_signatures": [sig1, sig2],
        }
    ]
    _, contents = GeminiLLM._convert_messages(messages)

    assert len(contents) == 1
    parts = contents[0].parts
    # 两个前置 thought Part + 一个 text Part
    assert len(parts) == 3
    assert parts[0].thought_signature == sig1
    assert parts[1].thought_signature == sig2
    assert parts[2].text == "好的"


# -----------------------------------------------------------------------
# 用例 5: round-trip 序列化/反序列化 pending_signatures
# -----------------------------------------------------------------------

@pytest.mark.unit
def test_pending_signatures_round_trip_through_session_store():
    """AssistantMessage(pending_signatures=[...]) →
    _serialize_messages → _deserialize_messages → pending_signatures 恢复。
    """
    original_sig = b"\x01\x02"
    messages_in = [
        UserMessage(content="用户输入"),
        AssistantMessage(content="回答", pending_signatures=[original_sig]),
    ]
    serialized = _serialize_messages(messages_in)
    restored = _deserialize_messages(serialized)

    assert len(restored) == 2
    assistant_out = restored[1]
    assert isinstance(assistant_out, AssistantMessage)
    assert assistant_out.pending_signatures == [original_sig]


# -----------------------------------------------------------------------
# 用例 6: end-to-end with real Gemini（external marker）
# -----------------------------------------------------------------------

@pytest.mark.external
def test_pending_signatures_round_trip_with_real_gemini():
    """构造 2 轮纯文本对话，把第 1 轮 text Part 上的 sig 通过 pending_signatures
    回传第 2 轮，验证不报错。

    仅当环境变量 GOOGLE_API_KEY 存在时跑，否则 skip。
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip("GOOGLE_API_KEY 未设置，跳过 external 测试")

    model = os.environ.get("GOOGLE_MODEL", "gemini-3.1-flash-lite-preview")
    config = GeminiConfig(api_key=api_key, model=model)
    llm = GeminiLLM(config)

    async def run():
        # 第 1 轮：收集 text Part 上的 thought_signature
        messages_round1 = [
            {"role": "user", "content": "请用一句话介绍自己。"},
        ]
        collected_sigs: list[bytes] = []
        text_tokens: list[str] = []
        async for chunk in llm.stream(messages_round1):
            if isinstance(chunk, TextChunk):
                text_tokens.append(chunk.token)
                if chunk.thought_signature is not None:
                    collected_sigs.append(chunk.thought_signature)

        first_reply = "".join(text_tokens)
        assert first_reply, "第 1 轮应有文本输出"

        # 第 2 轮：把第 1 轮的 pending_signatures 放入 assistant dict 回传
        messages_round2 = [
            {"role": "user", "content": "请用一句话介绍自己。"},
            {
                "role": "assistant",
                "content": first_reply,
                "pending_signatures": collected_sigs,
            },
            {"role": "user", "content": "谢谢！"},
        ]
        second_tokens: list[str] = []
        async for chunk in llm.stream(messages_round2):
            if isinstance(chunk, TextChunk):
                second_tokens.append(chunk.token)

        second_reply = "".join(second_tokens)
        assert second_reply, "第 2 轮应有文本输出"

    asyncio.run(run())
