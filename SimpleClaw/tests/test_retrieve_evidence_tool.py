import json
import unittest
from dataclasses import dataclass

from simpleclaw.llm.chunks import TextChunk

from Mojing.tools.retrieve_evidence import RetrieveEvidenceTool


@dataclass
class MemoryItem:
    key: str
    content: str
    description: str = ""


class FakeLLM:
    async def stream_with_retry(self, *_, **__):
        if False:
            yield None


class SelectingLLM:
    def __init__(self, output: str = "[1]") -> None:
        self.output = output
        self.prompts: list[str] = []

    async def stream_with_retry(self, messages, *_, **__):
        self.prompts.append(messages[0]["content"])
        yield TextChunk(self.output)


class FakeMemory:
    async def retrieve(self, top_k: int = 20):
        del top_k
        return [
            MemoryItem(
                key="sunscreen",
                description="用户之前询问过防晒推荐",
                content="用户偏好清爽、不搓泥、适合日常通勤的防晒。",
            )
        ]


class FakeImageRepo:
    def __init__(self) -> None:
        self.calls = 0

    async def get_latest_record_excluding(self, tenant_key: str, *, exclude_refs=None):
        del tenant_key, exclude_refs
        self.calls += 1
        return {
            "job_id": "job_1",
            "image_id": "img_1",
            "image_ref": "https://example.com/history.png",
            "status": "succeeded",
            "created_at": "2026-05-01 10:00:00",
        }


class RetrieveEvidenceToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_text_memory_route_uses_memory_retrieval(self):
        tool = RetrieveEvidenceTool(
            llm=FakeLLM(),  # type: ignore[arg-type]
            memory=FakeMemory(),  # type: ignore[arg-type]
            image_repo=FakeImageRepo(),  # type: ignore[arg-type]
        )
        tool.set_context(
            tenant_key="tenant_a",
            session_key="main:tenant_a",
            query="你之前推荐我的防晒是哪种来着？",
        )

        result = await tool.execute()
        payload = json.loads(result.content)

        self.assertEqual(payload["route"], "text_memory")
        self.assertEqual(payload["evidence_type"], "memory")
        self.assertIn("召回理由", payload["content"])
        self.assertIn("防晒", payload["content"])

    async def test_text_memory_selector_prompt_includes_content_preview(self):
        llm = SelectingLLM()
        tool = RetrieveEvidenceTool(
            llm=llm,  # type: ignore[arg-type]
            memory=FakeMemory(),  # type: ignore[arg-type]
            image_repo=FakeImageRepo(),  # type: ignore[arg-type]
        )
        tool.set_context(
            tenant_key="tenant_a",
            session_key="main:tenant_a",
            query="你之前推荐我的防晒是哪种来着？",
        )

        result = await tool.execute()
        payload = json.loads(result.content)

        self.assertIn("content_preview:", llm.prompts[0])
        self.assertIn("适合日常通勤", llm.prompts[0])
        self.assertIn("召回理由", payload["content"])

    async def test_historical_image_route_returns_image_fetched_payload(self):
        image_repo = FakeImageRepo()
        tool = RetrieveEvidenceTool(
            llm=FakeLLM(),  # type: ignore[arg-type]
            memory=FakeMemory(),  # type: ignore[arg-type]
            image_repo=image_repo,  # type: ignore[arg-type]
        )
        tool.set_context(
            tenant_key="tenant_a",
            session_key="main:tenant_a",
            query="你再看一下我之前那张照片，脸颊痘印明显吗？",
        )

        result = await tool.execute()
        payload = json.loads(result.content)

        self.assertEqual(payload["action"], "image_fetched")
        self.assertEqual(payload["route"], "historical_image")
        self.assertEqual(payload["evidence_type"], "image")
        self.assertEqual(payload["image_url"], "https://example.com/history.png")
        self.assertEqual(image_repo.calls, 1)

    async def test_explicit_historical_image_route_supports_subagent_prompt(self):
        tool = RetrieveEvidenceTool(
            llm=FakeLLM(),  # type: ignore[arg-type]
            memory=FakeMemory(),  # type: ignore[arg-type]
            image_repo=FakeImageRepo(),  # type: ignore[arg-type]
        )
        tool.set_context(
            tenant_key="tenant_a",
            session_key="skin_diary:tenant_a",
            query="我重拍好了，你再复核一下",
            media=["https://example.com/current.png"],
        )

        result = await tool.execute(route="historical_image")
        payload = json.loads(result.content)

        self.assertEqual(payload["route"], "historical_image")
        self.assertEqual(payload["reason"], "tool_argument")
        self.assertEqual(payload["image_url"], "https://example.com/history.png")


if __name__ == "__main__":
    unittest.main()
