"""Tests for main-agent attention boundaries around image/runtime status."""

from __future__ import annotations

import unittest

from Mojing.agent.main_agent import MainAgent
from simpleclaw.context.builder import ContextBuilder
from simpleclaw.core.messages import UserMessage


class _RuntimeTaskRepo:
    def __init__(self, latest=None, by_type=None) -> None:
        self.latest = latest
        self.by_type = by_type or {}

    async def find_latest_task_for(self, *, tenant_key: str, task_type: str):
        self.last_query = (tenant_key, task_type)
        if task_type in self.by_type:
            return self.by_type[task_type]
        return self.latest


class _DocRepo:
    async def get(self, tenant_key: str, doc_name: str) -> str | None:
        del tenant_key, doc_name
        return None


class _ImageRepo:
    def __init__(self, latest_job=None) -> None:
        self.latest_job = latest_job

    async def get_latest_time(self, tenant_key: str):
        del tenant_key
        return None

    async def find_latest_job(self, tenant_key: str):
        del tenant_key
        return self.latest_job


class MainAgentImageStatusTest(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_status_is_not_pushed_as_prompt_context(self) -> None:
        agent = MainAgent.__new__(MainAgent)
        agent._document_repo = _DocRepo()
        agent._image_repo = _ImageRepo()
        agent._tenant_state_repo = None
        agent._runtime_task_repo = _RuntimeTaskRepo(
            by_type={
                "image_analysis": {
                    "status": "running",
                    "updated_at": "2026-04-29 05:00:00",
                },
                "deep_research": {
                    "status": "wait_external",
                    "updated_at": "2026-04-29 05:01:00",
                },
            }
        )
        agent._action_usage_repo = None
        agent._skincare_cabinet_repo = None
        agent._deep_report_repo = None
        agent._skin_profile_repo = None
        agent._tool_invocation_store = None
        agent._deep_report_gate_attention_state = {}
        agent._deep_report_outcome_attention_state = {}
        agent._skincare_cabinet_task_attention_state = {}
        agent._skin_diary_task_attention_state = {}
        agent._skin_diary_completion_attention_state = {}
        agent._image_analysis_failure_attention_state = {}

        builder = ContextBuilder(
            ["stable"],
            dynamic_context_providers=agent._base_dynamic_context_providers(),
            attention_providers=agent._attention_providers(),
            tenant_key="tenant-1",
        )
        messages = await builder.build(
            [UserMessage("帮我看看这张图")],
            metadata={"image_just_uploaded": True},
            query="帮我看看这张图",
        )
        rendered_text = "\n\n".join(str(message.get("content") or "") for message in messages)

        self.assertIn("本轮上传了图片", rendered_text)
        self.assertIn("必要时调用 analyze_image", rendered_text)
        self.assertIn("模糊", rendered_text)
        self.assertNotIn("图片分析状态", rendered_text)
        self.assertNotIn("深度分析报告状态", rendered_text)


if __name__ == "__main__":
    unittest.main()
