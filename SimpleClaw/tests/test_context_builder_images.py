import json
import unittest

from simpleclaw.context.builder import ContextBuilder
from simpleclaw.core.messages import AssistantMessage, ToolCall, ToolResultMessage, UserMessage


class ContextBuilderImageHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_token_opener_is_skipped_after_formal_assistant_reply(self):
        builder = ContextBuilder(["stable"], tenant_key="tenant_a")
        history = [
            UserMessage("摸起来硬硬的，有点痛，还没冒白头"),
            AssistantMessage("知道啦"),
            AssistantMessage("我看一下之前那张照片。"),
            UserMessage("那今天怎么处理？"),
        ]

        messages = await builder.build(history)
        assistant_messages = [m for m in messages if m["role"] == "assistant"]

        self.assertEqual([m["content"] for m in assistant_messages], ["我看一下之前那张照片。"])

    async def test_current_turn_first_token_opener_is_kept_for_continuation(self):
        builder = ContextBuilder(["stable"], tenant_key="tenant_a")
        history = [
            UserMessage("摸起来硬硬的，有点痛，还没冒白头"),
            AssistantMessage("知道啦"),
        ]

        messages = await builder.build(history)
        assistant_messages = [m for m in messages if m["role"] == "assistant"]

        self.assertEqual([m["content"] for m in assistant_messages], ["知道啦"])

    async def test_current_turn_first_token_opener_is_kept_across_react_followup(self):
        builder = ContextBuilder(["stable"], tenant_key="tenant_a")
        history = [
            UserMessage("你漏了我下巴的痘痘啊"),
            AssistantMessage("知道啦"),
            AssistantMessage(
                "",
                [ToolCall(id="call-1", name="load_skill", arguments={"name": "skin_diary.current_concern_review"})],
            ),
            ToolResultMessage(call_id="call-1", content="场景 skill 已激活。"),
        ]

        messages = await builder.build(history)
        assistant_messages = [m for m in messages if m["role"] == "assistant"]

        self.assertEqual(assistant_messages[0]["content"], "知道啦")
        self.assertEqual(assistant_messages[1]["tool_calls"][0]["function"]["name"], "load_skill")

    async def test_historical_images_are_replaced_with_placeholder(self):
        builder = ContextBuilder(["stable"], tenant_key="tenant_a")
        history = [
            UserMessage([
                {"type": "text", "text": "我传一张正脸照片"},
                {
                    "type": "image_url",
                    "image_url": {"url": "https://example.com/face.png"},
                },
            ]),
            AssistantMessage("看到了。"),
            UserMessage("现在说说我的鼻翼泛红。"),
        ]

        messages = await builder.build(history)
        user_messages = [m for m in messages if m["role"] == "user"]

        self.assertEqual(user_messages[0]["content"], "我传一张正脸照片\n[用户已上传图片]")
        self.assertNotIn("https://example.com/face.png", user_messages[0]["content"])
        self.assertEqual(user_messages[1]["content"], "现在说说我的鼻翼泛红。")

    async def test_current_turn_image_is_kept(self):
        builder = ContextBuilder(["stable"], tenant_key="tenant_a")
        image_content = [
            {"type": "text", "text": "我传一张正脸照片"},
            {
                "type": "image_url",
                "image_url": {"url": "https://example.com/face.png"},
            },
        ]

        messages = await builder.build([UserMessage(image_content)])
        user_messages = [m for m in messages if m["role"] == "user"]

        self.assertIs(user_messages[0]["content"], image_content)

    async def test_retrieve_evidence_image_result_injects_model_visible_image(self):
        builder = ContextBuilder(["stable"], tenant_key="tenant_a")
        payload = {
            "ok": True,
            "action": "image_fetched",
            "image_url": "https://example.com/history.png",
            "uploaded_at": "2026-05-04 10:00:00",
            "source": "latest_known",
            "message_focus": "这是一张用户之前上传的皮肤照。",
        }
        history = [
            UserMessage("你看一下我之前那张图，痘印还明显吗？"),
            AssistantMessage(
                "我看一下之前那张照片。",
                [ToolCall(id="call-1", name="retrieve_evidence", arguments={"route": "historical_image"})],
            ),
            ToolResultMessage(call_id="call-1", content=json.dumps(payload, ensure_ascii=False)),
        ]

        messages = await builder.build(history)

        self.assertEqual(messages[-1]["role"], "user")
        self.assertIsInstance(messages[-1]["content"], list)
        self.assertEqual(messages[-1]["content"][1]["image_url"]["url"], "https://example.com/history.png")

    async def test_old_retrieve_evidence_image_result_does_not_reinject_image_after_new_user_turn(self):
        builder = ContextBuilder(["stable"], tenant_key="tenant_a")
        payload = {
            "ok": True,
            "action": "image_fetched",
            "image_url": "https://example.com/history.png",
        }
        history = [
            UserMessage("你看一下我之前那张图，痘印还明显吗？"),
            AssistantMessage(
                "我看一下之前那张照片。",
                [ToolCall(id="call-1", name="retrieve_evidence", arguments={"route": "historical_image"})],
            ),
            ToolResultMessage(call_id="call-1", content=json.dumps(payload, ensure_ascii=False)),
            AssistantMessage("看到了，痘印不算突兀。"),
            UserMessage("那我今天怎么护肤？"),
        ]

        messages = await builder.build(history)

        image_contexts = [
            m for m in messages
            if isinstance(m.get("content"), list)
            and any(part.get("image_url", {}).get("url") == "https://example.com/history.png"
                    for part in m["content"] if isinstance(part, dict))
        ]
        self.assertEqual(image_contexts, [])


if __name__ == "__main__":
    unittest.main()
