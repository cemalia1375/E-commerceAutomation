import pytest

from Mojing.agent.cold_path import _llm_complete_system_user
from simpleclaw.llm.chunks import TextChunk


class _FakePrefixLLM:
    def __init__(self):
        self.calls = []

    async def stream_with_retry(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        yield TextChunk('{"ok": true}')

    async def complete_session(self, **kwargs):
        raise AssertionError("cold path should not use session cache")


@pytest.mark.asyncio
async def test_cold_path_uses_prefix_cache_messages():
    llm = _FakePrefixLLM()

    result = await _llm_complete_system_user(
        llm,
        system="system prompt",
        user="dynamic payload",
        max_tokens=300,
        cache_tenant_key="__cold_path__",
    )

    assert result == '{"ok": true}'
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["max_tokens"] == 300
    assert call["temperature"] == 0.0

    system_message, user_message = call["messages"]
    assert system_message["role"] == "system"
    assert system_message["content"] == "system prompt"
    assert system_message["_cache_stable_prefix"] == "system prompt"
    assert system_message["_cache_dynamic_tail"] == ""
    assert system_message["_cache_tenant_key"] == "__cold_path__"
    assert system_message["_cache_session_key"] == "__shared__"
    assert system_message["_cache_lane"] == "cold_path"
    assert user_message == {"role": "user", "content": "dynamic payload"}
