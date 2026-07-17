import unittest

from simpleclaw.context import (
    AttentionPacket,
    ContextBuildContext,
    ContextBuilder,
    ContextSection,
    PromptSection,
)
from simpleclaw.core.messages import UserMessage
from Mojing.evidence import route_evidence_query


class StableProvider:
    async def collect_stable_prompt(self, ctx: ContextBuildContext) -> list[PromptSection]:
        return [PromptSection(content=f"# Stable for {ctx.tenant_key}", source="test")]


class DynamicProvider:
    async def collect_dynamic_context(self, ctx: ContextBuildContext) -> list[ContextSection]:
        return [ContextSection(content=f"# Dynamic query={ctx.query}", source="test")]


class AttentionProvider:
    async def collect_attention(self, ctx: ContextBuildContext) -> list[AttentionPacket]:
        return [
            AttentionPacket(
                content="low priority",
                source="test_low",
                priority=90,
                placement="before_last_user",
            ),
            AttentionPacket(
                content="high priority",
                source="test_high",
                priority=10,
                placement="before_last_user",
            ),
        ]


class ChangingAttentionProvider:
    def __init__(self) -> None:
        self.value = "v1"

    async def collect_attention(self, ctx: ContextBuildContext) -> list[AttentionPacket]:
        del ctx
        return [
            AttentionPacket(
                content=f"topic reminder {self.value}",
                source="topic_reminder",
                placement="before_last_user",
                lifetime="until_changed",
            )
        ]


class PeriodicAttentionProvider:
    async def collect_attention(self, ctx: ContextBuildContext) -> list[AttentionPacket]:
        del ctx
        return [
            AttentionPacket(
                content="periodic note",
                source="periodic_note",
                placement="before_last_user",
                lifetime="periodic",
                metadata={"interval": 2},
            )
        ]


class ContextProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_sections_are_rendered_with_explicit_sections(self):
        builder = ContextBuilder(
            ["# Legacy Stable"],
            stable_prompt_providers=[StableProvider()],
            dynamic_context_providers=[DynamicProvider()],
            tenant_key="tenant_a",
        )

        messages = await builder.build(
            [UserMessage("hello")],
            dynamic_context_sections=[
                ContextSection(content="# Explicit Dynamic", source="test")
            ],
            query="hello",
        )

        system = messages[0]
        self.assertEqual(system["role"], "system")
        self.assertIn("# Legacy Stable", system["content"])
        self.assertIn("# Stable for tenant_a", system["content"])
        self.assertIn("# Explicit Dynamic", system["content"])
        self.assertIn("# Dynamic query=hello", system["content"])
        self.assertEqual(system["_cache_tenant_key"], "tenant_a")

    async def test_attention_packets_are_sorted_and_inserted_before_last_user(self):
        builder = ContextBuilder(
            ["stable"],
            attention_providers=[AttentionProvider()],
            tenant_key="tenant_a",
        )

        messages = await builder.build([UserMessage("hello")])

        self.assertEqual(messages[1]["role"], "system")
        self.assertEqual(messages[1]["content"], "high priority")
        self.assertEqual(messages[2]["role"], "system")
        self.assertEqual(messages[2]["content"], "low priority")
        self.assertEqual(messages[3]["role"], "user")
        self.assertEqual(messages[3]["content"], "hello")

    async def test_explicit_attention_packets_are_inserted_by_placement(self):
        builder = ContextBuilder(["stable"], tenant_key="tenant_a")

        messages = await builder.build(
            [UserMessage("hello")],
            attention_packets=[
                AttentionPacket(
                    content="remember this",
                    source="test_reminder",
                    priority=20,
                    placement="before_last_user",
                ),
                AttentionPacket(
                    content="tail note",
                    source="test_tail",
                    priority=80,
                    placement="tail",
                ),
            ],
        )

        self.assertEqual(messages[1]["role"], "system")
        self.assertEqual(messages[1]["content"], "remember this")
        self.assertEqual(messages[2]["role"], "user")
        self.assertEqual(messages[2]["content"], "hello")
        self.assertEqual(messages[3]["role"], "system")
        self.assertEqual(messages[3]["content"], "tail note")

    async def test_until_changed_attention_is_emitted_only_when_content_changes(self):
        provider = ChangingAttentionProvider()
        builder = ContextBuilder(
            ["stable"],
            attention_providers=[provider],
            tenant_key="tenant_a",
        )

        first = await builder.build([UserMessage("hello")])
        second = await builder.build([UserMessage("again")])
        provider.value = "v2"
        third = await builder.build([UserMessage("changed")])

        self.assertTrue(any(m.get("content") == "topic reminder v1" for m in first))
        self.assertFalse(any(m.get("content") == "topic reminder v1" for m in second))
        self.assertTrue(any(m.get("content") == "topic reminder v2" for m in third))

    async def test_periodic_attention_is_emitted_on_interval(self):
        builder = ContextBuilder(
            ["stable"],
            attention_providers=[PeriodicAttentionProvider()],
            tenant_key="tenant_a",
        )

        first = await builder.build([UserMessage("one")])
        second = await builder.build([UserMessage("two")])
        third = await builder.build([UserMessage("three")])

        self.assertTrue(any(m.get("content") == "periodic note" for m in first))
        self.assertTrue(any(m.get("content") == "periodic note" for m in second))
        self.assertFalse(any(m.get("content") == "periodic note" for m in third))

    async def test_historical_image_query_is_not_memory_recall(self):
        self.assertNotEqual(route_evidence_query("你再看一下我之前那张照片，我是不是有痘印？").kind, "text_memory")
        self.assertEqual(route_evidence_query("你之前给我推荐过什么？").kind, "text_memory")
        self.assertEqual(route_evidence_query("你再看一下我之前那张照片，我是不是有痘印？").kind, "historical_image")
        self.assertEqual(route_evidence_query("我感觉我有点痘印，你发现了吗？").kind, "historical_image")
        self.assertEqual(route_evidence_query("你看看我皮肤状态", has_current_media=True).kind, "none")
        self.assertEqual(route_evidence_query("你看看我之前那张照片", has_current_media=True).kind, "historical_image")
        self.assertEqual(route_evidence_query("你之前给我推荐过什么？").kind, "text_memory")
        self.assertEqual(route_evidence_query("痘印怎么淡一点？").kind, "none")


if __name__ == "__main__":
    unittest.main()
