"""Regression tests for Skin Diary Markdown concern state and tools."""

from __future__ import annotations

import sys
import types
import unittest

try:
    import loguru  # noqa: F401
except ModuleNotFoundError:
    sys.modules.setdefault("loguru", types.SimpleNamespace(logger=types.SimpleNamespace(
        info=lambda *_, **__: None,
        debug=lambda *_, **__: None,
        warning=lambda *_, **__: None,
        error=lambda *_, **__: None,
    )))

try:
    import aiomysql  # noqa: F401
except ModuleNotFoundError:
    sys.modules.setdefault(
        "aiomysql",
        types.SimpleNamespace(
            create_pool=None,
            Pool=object,
            pool=types.SimpleNamespace(_PoolConnectionContextManager=object),
        ),
    )

from simpleclaw.context.providers import ContextBuildContext
from Mojing.context.skin_diary import SkinDiaryHandoffContractAttentionProvider
from Mojing.subagent.skin_diary import SkinDiarySubagent


class _FakeImageRepo:
    async def get_latest_record_excluding(self, tenant_key: str, exclude_refs: list[str] | None = None):
        del tenant_key, exclude_refs
        return {"image_ref": "original-image-url"}


class _FakeDocumentRepo:
    def __init__(self, docs: dict[str, str] | None = None) -> None:
        self.docs = docs or {
            "USER.md": (
                "## skin\n\n### current_concerns\n\n"
                "- 下巴闭口｜来源：用户主诉关注｜状态：待确认｜不作为图片检测结论"
            )
        }

    async def get(self, tenant_key: str, doc_name: str) -> str | None:
        del tenant_key
        return self.docs.get(doc_name)


class _FakeResultRepo:
    def __init__(
        self,
        latest: dict | None = None,
        history: list[dict] | None = None,
    ) -> None:
        self.latest = latest
        self.history = history or ([latest] if latest else [])

    async def get_latest(self, tenant_key: str):
        del tenant_key
        return self.latest

    async def get_results_for_business_date(self, tenant_key: str, business_date):
        del tenant_key, business_date
        return list(self.history)


class _FakeRuntimeTaskRepo:
    def __init__(self, status: str = "succeeded") -> None:
        self.status = status

    async def find_latest_task_for(self, *, tenant_key: str, task_type: str):
        del tenant_key, task_type
        return {
            "task_id": "task-1",
            "status": self.status,
            "updated_at": "2026-05-01 10:00:00",
            "completed_at": "2026-05-01 10:00:00" if self.status == "succeeded" else None,
            "last_error": None,
        }


class _FakeTopicRepo:
    async def get(self, topic_key: str):
        del topic_key
        return None


async def _collect_dynamic_sections(agent: SkinDiarySubagent, query: str = ""):
    ctx = ContextBuildContext(history=[], query=query, tenant_key="tenant-1")
    sections = []
    for provider in agent.make_dynamic_context_providers("tenant-1"):
        sections.extend(await provider.collect_dynamic_context(ctx))
    return sections


async def _collect_attention_packets(agent: SkinDiarySubagent, query: str = ""):
    ctx = ContextBuildContext(history=[], query=query, tenant_key="tenant-1")
    packets = []
    for provider in agent.make_attention_providers("tenant-1"):
        packets.extend(await provider.collect_attention(ctx))
    return packets


class SkinDiaryMarkdownStateTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_registry_registers_retrieve_evidence_when_image_repo_exists(self) -> None:
        agent = object.__new__(SkinDiarySubagent)
        agent._llm = None  # type: ignore[attr-defined]
        agent._db = None  # type: ignore[attr-defined]
        agent._document_repo = _FakeDocumentRepo()  # type: ignore[attr-defined]
        agent._skin_profile_repo = object()  # type: ignore[attr-defined]
        agent._result_repo = _FakeResultRepo()  # type: ignore[attr-defined]
        agent._image_repo = _FakeImageRepo()  # type: ignore[attr-defined]
        agent._runtime_task_repo = None  # type: ignore[attr-defined]
        agent._skincare_cabinet_repo = None  # type: ignore[attr-defined]
        agent._crop_endpoint_url = ""  # type: ignore[attr-defined]
        agent._crop_timeout_s = 20  # type: ignore[attr-defined]

        registry = agent.make_tool_registry("tenant-1")

        self.assertIn("load_skill", registry.tool_names)
        self.assertIn("unload_skill", registry.tool_names)
        self.assertIn("generate_skin_diary", registry.tool_names)
        self.assertIn("retrieve_evidence", registry.tool_names)
        schema_names = [item["function"]["name"] for item in registry.schemas()]
        self.assertIn("load_skill", schema_names)
        self.assertIn("unload_skill", schema_names)

    async def test_tool_registry_skips_retrieve_evidence_without_image_repo(self) -> None:
        agent = object.__new__(SkinDiarySubagent)
        agent._llm = None  # type: ignore[attr-defined]
        agent._db = None  # type: ignore[attr-defined]
        agent._document_repo = _FakeDocumentRepo()  # type: ignore[attr-defined]
        agent._skin_profile_repo = object()  # type: ignore[attr-defined]
        agent._result_repo = _FakeResultRepo()  # type: ignore[attr-defined]
        agent._image_repo = None  # type: ignore[attr-defined]
        agent._runtime_task_repo = None  # type: ignore[attr-defined]
        agent._skincare_cabinet_repo = None  # type: ignore[attr-defined]
        agent._crop_endpoint_url = ""  # type: ignore[attr-defined]
        agent._crop_timeout_s = 20  # type: ignore[attr-defined]

        registry = agent.make_tool_registry("tenant-1")

        self.assertIn("generate_skin_diary", registry.tool_names)
        self.assertNotIn("retrieve_evidence", registry.tool_names)

    async def test_user_md_current_concerns_are_injected_from_markdown(self) -> None:
        agent = object.__new__(SkinDiarySubagent)
        agent._document_repo = _FakeDocumentRepo()  # type: ignore[attr-defined]
        agent._result_repo = _FakeResultRepo()  # type: ignore[attr-defined]

        sections = await _collect_dynamic_sections(agent)

        self.assertEqual(len(sections), 1)
        self.assertIn("current_concerns", sections[0].content)
        self.assertIn("下巴闭口", sections[0].content)

    async def test_skin_diary_todo_document_is_injected(self) -> None:
        agent = object.__new__(SkinDiarySubagent)
        agent._document_repo = _FakeDocumentRepo({
            "USER.md": "## skin\n\n（无内容）",
            "SKIN_DIARY_TODO.md": "## active\n\n- [ ] due: tonight｜观察下巴闭口",
        })  # type: ignore[attr-defined]
        agent._result_repo = _FakeResultRepo()  # type: ignore[attr-defined]

        sections = await _collect_dynamic_sections(agent)

        joined = "\n".join(section.content for section in sections)
        self.assertIn("用户皮肤任务", joined)
        self.assertIn("观察下巴闭口", joined)

    async def test_first_generation_without_result_injects_no_diary_result(self) -> None:
        agent = object.__new__(SkinDiarySubagent)
        agent._document_repo = _FakeDocumentRepo()  # type: ignore[attr-defined]
        agent._result_repo = _FakeResultRepo()  # type: ignore[attr-defined]
        agent._runtime_task_repo = _FakeRuntimeTaskRepo(status="wait_external")  # type: ignore[attr-defined]

        sections = await _collect_dynamic_sections(agent)

        joined = "\n".join(section.content for section in sections)
        self.assertNotIn("肌肤日记生成任务状态", joined)
        self.assertNotIn("当前可用肌肤日记分析", joined)

    async def test_completed_generation_injects_latest_diary_result(self) -> None:
        latest = {
            "state": "stable",
            "summary": "新版日记摘要",
            "analyzed_at": "2026-05-02 09:00:00",
            "diary_date": "2026-05-02",
            "diary_slot": "morning",
            "morning_steps": [{"order": 1, "title": "清洁", "usage": "温和洁面", "effect": "减少刺激"}],
        }
        agent = object.__new__(SkinDiarySubagent)
        agent._document_repo = _FakeDocumentRepo()  # type: ignore[attr-defined]
        agent._result_repo = _FakeResultRepo(latest=latest)  # type: ignore[attr-defined]
        agent._runtime_task_repo = _FakeRuntimeTaskRepo(status="succeeded")  # type: ignore[attr-defined]

        sections = await _collect_dynamic_sections(agent)

        joined = "\n".join(section.content for section in sections)
        self.assertIn("当前可用肌肤日记分析", joined)
        self.assertIn("新版日记摘要", joined)
        self.assertIn("晨间护肤步骤", joined)
        self.assertNotIn("新版生成中", joined)

    async def test_active_generation_marks_injected_diary_as_existing_result(self) -> None:
        latest = {
            "state": "stable",
            "summary": "旧版日记摘要",
            "analyzed_at": "2026-05-01 09:00:00",
            "diary_date": "2026-05-01",
            "diary_slot": "morning",
        }
        agent = object.__new__(SkinDiarySubagent)
        agent._document_repo = _FakeDocumentRepo()  # type: ignore[attr-defined]
        agent._result_repo = _FakeResultRepo(latest=latest)  # type: ignore[attr-defined]
        agent._runtime_task_repo = _FakeRuntimeTaskRepo(status="wait_external")  # type: ignore[attr-defined]

        sections = await _collect_dynamic_sections(agent)

        joined = "\n".join(section.content for section in sections)
        self.assertIn("这份是新版生成任务完成前的现有结果", joined)
        self.assertIn("不代表新版已经生成完成", joined)
        self.assertIn("当前可用肌肤日记分析（新版生成中）", joined)
        self.assertNotIn("## 最新肌肤日记分析", joined)

    async def test_handoff_chat_intent_does_not_force_generation_tool(self) -> None:
        provider = SkinDiaryHandoffContractAttentionProvider()
        ctx = ContextBuildContext(
            history=[],
            query="帮我解释一下今天的肌肤日记",
            tenant_key="tenant-1",
            metadata={
                "handoff_contract": {
                    "kind": "skin_diary",
                    "intent": "chat",
                },
            },
        )

        packets = await provider.collect_attention(ctx)

        self.assertEqual(len(packets), 1)
        self.assertIn("肌肤日记转交", packets[0].content)
        self.assertIn("不要主动调用 `generate_skin_diary`", packets[0].content)
        self.assertNotIn("本轮优先目标：直接调用 `generate_skin_diary`", packets[0].content)

    async def test_handoff_generate_intent_forces_generation_tool(self) -> None:
        provider = SkinDiaryHandoffContractAttentionProvider()
        ctx = ContextBuildContext(
            history=[],
            query="帮我刷新今天的肌肤日记",
            tenant_key="tenant-1",
            metadata={
                "handoff_contract": {
                    "kind": "skin_diary",
                    "intent": "handoff",
                    "required_tool": "generate_skin_diary",
                    "show_existing_first": False,
                    "forbid_claiming_completion_without_tool": True,
                },
            },
        )

        packets = await provider.collect_attention(ctx)

        self.assertEqual(len(packets), 1)
        self.assertIn("本轮优先目标：直接调用 `generate_skin_diary`", packets[0].content)
        self.assertIn("不要先展示已有肌肤日记", packets[0].content)

if __name__ == "__main__":
    unittest.main()
