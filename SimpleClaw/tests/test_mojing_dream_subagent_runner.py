import json

import pytest

from simpleclaw.dream import DreamJob
from simpleclaw.llm.chunks import TextChunk, ToolCallChunk
from simpleclaw.memory.ledger import MemoryLedgerRecord, MemorySnapshot
from Mojing.dream import MojingDreamSubagentRunner


class _LedgerRepo:
    def __init__(self, ledger: MemoryLedgerRecord | None) -> None:
        self.ledger = ledger
        self.updates = []

    async def get_ledger(self, ledger_id: str):
        if self.ledger and self.ledger.ledger_id == ledger_id:
            return self.ledger
        return None

    async def update_ledger(self, ledger_id: str, **kwargs):
        self.updates.append((ledger_id, kwargs))
        if self.ledger and self.ledger.ledger_id == ledger_id and "dream_status" in kwargs:
            self.ledger.dream_status = kwargs["dream_status"]
        return self.ledger


class _SessionRepo:
    async def load_messages(self, tenant_key: str, session_key: str):
        return [
            {"role": "user", "content": "最近熬夜后黑眼圈明显。"},
            {"role": "assistant", "content": "我会记录你的作息和眼周状态。"},
        ], 0


class _DocumentRepo:
    async def get(self, tenant_key: str, doc_name: str):
        return f"# {doc_name}\n- 熬夜后黑眼圈明显"

    async def get_metadata(self, tenant_key: str, doc_name: str):
        return {"content_hash": "hash", "updated_at": "2026-06-06 12:00:00"}

    async def list_recent_versions_for_session(self, *, tenant_key: str, session_key: str, limit: int = 10):
        return [{"doc_name": "USER.md", "version_no": 1}]


class _RuntimeTaskRepo:
    async def list_recent(self, *, tenant_key: str = "", limit: int = 20):
        return [{"task_id": "task_1", "task_type": "image_analysis", "status": "succeeded"}]


class _FakeDreamLLM:
    def __init__(self) -> None:
        self.calls = 0
        self.seen_tools = []

    async def stream_with_retry(self, messages, tools=None, **kwargs):
        self.calls += 1
        self.seen_tools.append([tool["function"]["name"] for tool in tools or []])
        if self.calls == 1:
            yield ToolCallChunk(id="toolcall_1", name="read_memory_ledger", arguments={})
            return
        yield TextChunk(json.dumps({
            "artifact_version": "mojing.dream_subagent.v1",
            "status": "draft",
            "summary": "ledger reviewed with tool evidence",
            "memory_review": {"missed_facts": ["应保留熬夜和黑眼圈关联"]},
            "document_review": {"user_md_suggestions": []},
            "runtime_fact_review": {"important_task_facts": []},
            "audit": {"evidence_read": ["read_memory_ledger"], "limitations": []},
            "confidence": "medium",
        }, ensure_ascii=False))


def _ledger() -> MemoryLedgerRecord:
    return MemoryLedgerRecord(
        ledger_id="memledger_1",
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        source="main",
        status="applied",
        message_seq_start=0,
        message_seq_end=39,
        last_consolidated_from=0,
        last_consolidated_to=40,
        source_chunk=[{"role": "user", "content": "最近熬夜后黑眼圈明显。"}],
        memory_before=MemorySnapshot(items=[]),
        memory_actions=[{"action": "create", "topic": "熬夜黑眼圈"}],
        memory_after=MemorySnapshot(items=[{"topic": "熬夜黑眼圈"}]),
        business_snapshot={"runtime_facts": [{"task_type": "image_analysis", "status": "succeeded"}]},
    )


def _job() -> DreamJob:
    return DreamJob(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        trigger="memory_threshold",
        reason="review ledger",
        candidate_id="dreamcand_1",
        source_id="memledger_1",
        input_cursor="0:40",
        job_id="dreamjob_1",
        payload={
            "signal": {
                "signal_id": "dreamsig_1",
                "signal_type": "memory_ledger_applied",
                "subject_type": "memory_ledger",
                "subject_id": "memledger_1",
            },
            "read_assets": ["memory_ledger", "session_messages", "memory_entries"],
            "write_assets": ["dream_artifact"],
            "forbidden_assets": [],
        },
    )


def _test_job() -> DreamJob:
    job = _job()
    job.tenant_key = "test_tenant_1"
    job.session_key = "main:test_tenant_1"
    return job


def _runner(*, ledger_repo, llm=None):
    return MojingDreamSubagentRunner(
        db=object(),
        llm=llm,
        memory_ledger_repo=ledger_repo,
        session_repo=_SessionRepo(),
        document_repo=_DocumentRepo(),
        runtime_task_repo=_RuntimeTaskRepo(),
    )


@pytest.mark.asyncio
async def test_dream_subagent_runner_fallback_writes_draft_artifact_without_llm():
    repo = _LedgerRepo(_ledger())

    result = await _runner(ledger_repo=repo)(_job())

    assert result.status == "succeeded"
    assert result.artifacts[0].status == "draft"
    assert result.artifacts[0].metadata["runner"] == "mojing_dream_subagent"
    assert result.artifacts[0].metadata["source_refs"]["memory_ledger_ids"] == ["memledger_1"]
    content = json.loads(result.artifacts[0].content)
    assert content["runner"] == "mojing_dream_subagent"
    assert content["audit"]["limitations"] == ["llm_not_configured"]
    assert repo.ledger.dream_status == "reviewed"


@pytest.mark.asyncio
async def test_dream_subagent_runner_uses_subagent_loop_and_tools():
    repo = _LedgerRepo(_ledger())
    llm = _FakeDreamLLM()

    result = await _runner(ledger_repo=repo, llm=llm)(_job())

    assert result.status == "succeeded"
    assert llm.calls == 2
    assert "read_memory_ledger" in llm.seen_tools[0]
    # upsert_memory_entry 现在始终注册（skin 类型写入对非测试租户也开放），但 write_document 仍仅限测试租户
    assert "upsert_memory_entry" in llm.seen_tools[0]
    assert "write_document" not in llm.seen_tools[0]
    content = json.loads(result.artifacts[0].content)
    assert content["summary"] == "ledger reviewed with tool evidence"
    assert content["audit"]["evidence_read"] == ["read_memory_ledger"]
    assert repo.ledger.dream_status == "reviewed"


@pytest.mark.asyncio
async def test_dream_subagent_exposes_write_tools_only_for_test_tenants():
    repo = _LedgerRepo(_ledger())
    llm = _FakeDreamLLM()

    result = await _runner(ledger_repo=repo, llm=llm)(_test_job())

    assert result.status == "succeeded"
    assert "upsert_memory_entry" in llm.seen_tools[0]
    assert "write_document" in llm.seen_tools[0]
    content = json.loads(result.artifacts[0].content)
    assert content["summary"] == "ledger reviewed with tool evidence"
