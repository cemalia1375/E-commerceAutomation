import asyncio

from Mojing.agent.first_token import (
    _build_shared_context,
    _infer_agent_lane,
    _prompt_path_for_lane,
    build_first_token_context_message,
    build_first_token_continuation_instruction,
    build_first_token_user_message,
    join_first_token_reply,
)
from Mojing.api.routes.chat import _insert_opener_message, _resolve_opener_text
from Mojing.storage.subagent_store import _resolve_opener_text as _resolve_subagent_opener_text
from simpleclaw.core.messages import AssistantMessage, ToolResultMessage, UserMessage


class _Loop:
    def __init__(self, messages):
        self.messages = messages


class _Result:
    def __init__(self, text: str):
        self.text = text


class _FirstTokenAgent:
    timeout_s = 0.01


class _Container:
    first_token_agent = _FirstTokenAgent()


def test_shared_context_uses_latest_assistant_text_and_skips_tool_results():
    messages = [
        UserMessage("之前的问题"),
        AssistantMessage("短开场"),
        AssistantMessage("", []),
        ToolResultMessage(call_id="call_1", content="工具结果不应该进 opener 上下文"),
        AssistantMessage("主 agent 最终回复"),
    ]

    ctx = _build_shared_context(messages, consolidated_from=0)

    assert ctx.last_assistant_reply == "主 agent 最终回复"
    assert "工具结果不应该进 opener 上下文" not in ctx.active_window_text
    assert "短开场" not in ctx.active_window_text
    assert "主 agent 最终回复" in ctx.active_window_text
    assert ctx.context_version == 0


def test_shared_context_uses_history_offset_as_context_version():
    messages = [
        UserMessage("新问题"),
        AssistantMessage("新回复"),
    ]

    ctx = _build_shared_context(messages, consolidated_from=0, history_offset=12)

    assert "新问题" in ctx.active_window_text
    assert ctx.context_version == 12


def test_shared_context_keeps_main_react_text_before_tool_result():
    messages = [
        UserMessage("帮我查一下之前的记录"),
        AssistantMessage("我先找一下之前的记录。"),
        ToolResultMessage(call_id="call_1", content="工具结果不应该进 opener 上下文"),
        AssistantMessage("找到啦，昨天说的是温和清洁。"),
    ]

    ctx = _build_shared_context(messages, consolidated_from=0)

    assert "我先找一下之前的记录。" in ctx.active_window_text
    assert "找到啦，昨天说的是温和清洁。" in ctx.active_window_text
    assert "工具结果不应该进 opener 上下文" not in ctx.active_window_text


def test_first_token_prompt_path_uses_agent_lane_files():
    assert _infer_agent_lane("main:334") == "main"
    assert _infer_agent_lane("skin_diary:334") == "skin_diary"
    assert _infer_agent_lane("deep-report:334") == "deep_report"
    assert _prompt_path_for_lane("skin_diary").name == "first_token.skin_diary.md"
    assert _prompt_path_for_lane("deep_report").name == "first_token.deep_report.md"
    assert _prompt_path_for_lane("unknown_lane").name == "first_token.md"
    assert _prompt_path_for_lane("main", "device").parent.name == "device"
    assert _prompt_path_for_lane("main", "device").name == "first_token.md"


def test_join_first_token_reply_keeps_bubble_boundary():
    assert join_first_token_reply("我先看看", "正式回复") == "我先看看\n正式回复"
    assert join_first_token_reply("", "正式回复") == "正式回复"
    assert join_first_token_reply("我先看看", "") == "我先看看"


def test_first_token_context_message_marks_visible_opener_as_already_sent():
    context = build_first_token_context_message("好呀，我这边马上处理啦。")
    instruction = build_first_token_continuation_instruction("好呀，我这边马上处理啦。")

    assert "已发送给用户的第一气泡" in context
    assert context.endswith("好呀，我这边马上处理啦。")
    assert "不要复述、改写或同义重复第一气泡" in instruction
    assert "只调用必要工具，不输出文本" in instruction
    assert join_first_token_reply("好呀，我这边马上处理啦。", "") == "好呀，我这边马上处理啦。"


def test_first_token_user_message_supports_pure_image_without_analysis_boundary():
    text = build_first_token_user_message("", ["https://example.com/a.jpg"])

    assert "用户本轮上传了图片" in text
    assert "不要描述图片内容" in text
    assert "不要判断清晰度" in text


def test_first_token_user_message_keeps_text_and_adds_media_signal():
    text = build_first_token_user_message("帮我看看", ["https://example.com/a.jpg"])

    assert text.startswith("帮我看看")
    assert "用户本轮上传了图片" in text


def test_insert_opener_message_after_current_user():
    loop = _Loop([
        UserMessage("旧问题"),
        AssistantMessage("旧回复"),
        UserMessage("新问题"),
        AssistantMessage("主回复"),
    ])

    _insert_opener_message(loop, messages_before=2, opener_text="我在听。")

    assert [type(m).__name__ for m in loop.messages] == [
        "UserMessage",
        "AssistantMessage",
        "UserMessage",
        "AssistantMessage",
        "AssistantMessage",
    ]
    assert loop.messages[3].content == "我在听。"
    assert loop.messages[4].content == "主回复"


def test_main_opener_waits_for_completion_after_partial_output():
    async def _run():
        buffer: list[str] = []

        async def _opener():
            buffer.append("我先")
            await asyncio.sleep(0.03)
            buffer.append("看看")
            return _Result("我先看看")

        task = asyncio.create_task(_opener())
        text, status, detail = await _resolve_opener_text(task, _Container(), buffer)

        assert text == "我先看看"
        assert status == "done_after_timeout"
        assert "first delta arrived" in detail
        assert not task.cancelled()

    asyncio.run(_run())


def test_subagent_opener_waits_for_completion_after_partial_output():
    async def _run():
        buffer: list[str] = []

        async def _opener():
            buffer.append("我先")
            await asyncio.sleep(0.03)
            buffer.append("看看")
            return _Result("我先看看")

        task = asyncio.create_task(_opener())
        text, status, detail = await _resolve_subagent_opener_text(
            opener_task=task,
            timeout_s=0.01,
            opener_buffer=buffer,
            tenant_key="tenant",
            session_key="skin_diary:tenant",
            subagent_name="skin_diary",
        )

        assert text == "我先看看"
        assert status == "done_after_timeout"
        assert "first delta arrived" in detail
        assert not task.cancelled()

    asyncio.run(_run())
