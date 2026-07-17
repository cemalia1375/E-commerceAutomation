"""Tests for tenant document injection in MainAgent."""

from __future__ import annotations

import unittest

from Mojing.agent.main_agent import MainAgent
from simpleclaw.context.builder import ContextBuilder
from simpleclaw.core.messages import UserMessage


class _DocRepo:
    def __init__(self, docs: dict[str, str]) -> None:
        self.docs = docs

    async def get(self, tenant_key: str, doc_name: str) -> str | None:
        del tenant_key
        return self.docs.get(doc_name)


class _ImageRepo:
    async def get_latest_time(self, tenant_key: str):
        del tenant_key
        return None


class MainAgentDocumentsTest(unittest.IsolatedAsyncioTestCase):
    async def test_context_builder_injects_tenant_soul(self) -> None:
        agent = MainAgent.__new__(MainAgent)
        agent._document_repo = _DocRepo(
            {
                "USER.md": "## style\n\n- 回复长度偏好：简短",
                "SOUL.md": "- 偏好直接说结论\n- 拒绝聊年龄",
            }
        )
        agent._image_repo = _ImageRepo()
        agent._runtime_task_repo = None
        agent._deep_report_repo = None

        builder = ContextBuilder(
            [],
            dynamic_context_providers=agent._base_dynamic_context_providers(),
            tenant_key="tenant-1",
        )
        messages = await builder.build([UserMessage("hello")])
        joined = messages[0]["content"]

        self.assertIn("## style", joined)
        self.assertIn("【用户沟通偏好 / 红线 · SOUL.md】", joined)
        self.assertIn("偏好直接说结论", joined)
        self.assertIn("拒绝聊年龄", joined)


if __name__ == "__main__":
    unittest.main()
