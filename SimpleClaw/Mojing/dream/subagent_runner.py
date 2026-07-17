"""Mojing Dream runner backed by a real SimpleClaw subagent loop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from simpleclaw.context.builder import ContextBuilder
from simpleclaw.context.providers import ContextSection
from simpleclaw.dream.protocol import DreamArtifact, DreamJob, DreamResult
from simpleclaw.llm.base import LLMProvider
from simpleclaw.subagent.runner import SubagentRunner
from simpleclaw.tools.registry import ToolRegistry

from Mojing.config import make_dream_mutation_enabled
from Mojing.dream.tools import (
    ReadDocumentTool,
    ReadDocumentVersionsTool,
    ReadMemoryEntriesTool,
    ReadMemoryLedgerTool,
    ReadRuntimeTasksTool,
    ReadSessionMessagesTool,
    UpsertMemoryEntryTool,
    WriteDocumentTool,
)
from Mojing.storage.database import Database
from Mojing.storage.document_repo import DocumentRepository
from Mojing.storage.memory_ledger_repo import MemoryLedgerRepository
from Mojing.storage.runtime_task_repo import RuntimeTaskRepository
from Mojing.storage.session_repo import SessionRepository


_DREAM_SUBAGENT_PROMPT_PATH = Path(__file__).parent.parent / "workspace" / "dream_subagent.md"


def load_dream_subagent_prompt() -> str:
    if _DREAM_SUBAGENT_PROMPT_PATH.exists():
        return _DREAM_SUBAGENT_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return _default_prompt()


class MojingDreamSubagentRunner:
    """Run one global Mojing DreamSubagent job.

    This runner intentionally uses the main-agent/global perspective. It does
    not split dream into per-memory-source subagents. The job signal tells the
    subagent why it woke up; tools let it inspect enough evidence before it
    writes a draft DreamArtifact.
    """

    def __init__(
        self,
        *,
        db: Database,
        llm: LLMProvider | None,
        memory_ledger_repo: MemoryLedgerRepository,
        session_repo: SessionRepository,
        document_repo: DocumentRepository,
        runtime_task_repo: RuntimeTaskRepository | None = None,
        skin_profile_repo: Any | None = None,
    ) -> None:
        self._db = db
        self._llm = llm
        self._memory_ledger_repo = memory_ledger_repo
        self._session_repo = session_repo
        self._document_repo = document_repo
        self._runtime_task_repo = runtime_task_repo
        self._skin_profile_repo = skin_profile_repo

    async def __call__(self, job: DreamJob) -> DreamResult:
        ledger = await self._load_source_ledger(job)
        if ledger is not None and ledger.status != "applied":
            await self._memory_ledger_repo.update_ledger(
                ledger.ledger_id,
                dream_status="not_needed",
                metadata={"dream_skip_reason": f"ledger status is {ledger.status}", "dream_job_id": job.job_id},
            )
            return DreamResult.skipped(job.job_id, summary=f"dream skipped: ledger status={ledger.status}")

        if ledger is not None:
            await self._memory_ledger_repo.update_ledger(
                ledger.ledger_id,
                dream_status="candidate",
                metadata={"dream_job_id": job.job_id, "dream_runner": "mojing_dream_subagent"},
            )

        try:
            content = await self._run_or_fallback(job, ledger)
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            if ledger is not None:
                await self._memory_ledger_repo.update_ledger(
                    ledger.ledger_id,
                    dream_status="failed",
                    last_error=error,
                    metadata={"dream_job_id": job.job_id, "dream_runner": "mojing_dream_subagent"},
                )
            raise

        artifact = DreamArtifact(
            job_id=job.job_id,
            artifact_type="memory_summary",
            content=content,
            key=_artifact_key(job, ledger),
            status="draft",
            metadata=_artifact_metadata(job, ledger),
        )
        if ledger is not None:
            await self._memory_ledger_repo.update_ledger(
                ledger.ledger_id,
                dream_status="reviewed",
                metadata={
                    "dream_job_id": job.job_id,
                    "dream_artifact_id": artifact.artifact_id,
                    "dream_runner": "mojing_dream_subagent",
                },
            )
        return DreamResult.succeeded(
            job.job_id,
            summary=_summary(job, ledger),
            artifacts=[artifact],
            metadata={
                "runner": "mojing_dream_subagent",
                "memory_ledger_id": getattr(ledger, "ledger_id", None),
                "signal_type": _signal(job).get("signal_type"),
            },
        )

    async def _load_source_ledger(self, job: DreamJob) -> Any | None:
        ledger_id = str(job.source_id or "").strip()
        if not ledger_id.startswith("memledger_"):
            return None
        ledger = await self._memory_ledger_repo.get_ledger(ledger_id)
        if ledger is None:
            return None
        return ledger

    async def _run_or_fallback(self, job: DreamJob, ledger: Any | None) -> str:
        allow_mutation = make_dream_mutation_enabled(job.tenant_key)
        packet = _job_packet(job, ledger, allow_mutation=allow_mutation)
        if self._llm is None:
            return json.dumps(_fallback_artifact(job, packet), ensure_ascii=False, indent=2)

        registry = self._build_tool_registry(job, ledger, allow_mutation=allow_mutation)
        runner = SubagentRunner(self._llm)
        context_builder = ContextBuilder(
            stable_sections=[load_dream_subagent_prompt()],
            tenant_key=job.tenant_key,
            cache_lane="dream",
            cache_session_key=job.session_key or f"main:{job.tenant_key}",
        )
        sections: list[ContextSection] = [
            ContextSection(
                content=json.dumps(packet, ensure_ascii=False, default=str, indent=2),
                source="dream_job_packet",
                metadata={"dream_job_id": job.job_id},
            )
        ]
        if self._skin_profile_repo is not None:
            try:
                from datetime import datetime, timedelta
                from Mojing.agent.skin_trend import compute_trends, render_trend_facts
                end = datetime.utcnow()
                start = end - timedelta(days=30)
                rows = await self._skin_profile_repo.list_profiles_in_range(job.tenant_key, start, end)
                trends = compute_trends(rows)
                if trends:
                    sections.append(ContextSection(
                        content=render_trend_facts(trends),
                        source="skin_trend_facts",
                        metadata={"dream_job_id": job.job_id},
                    ))
            except Exception as exc:
                logger.warning("dream skin trend build failed job={}: {}", job.job_id, exc)
        messages, reply = await runner.run_turn(
            "Run Mojing DreamSubagent review for the provided DreamJob. Return strict JSON only.",
            history=[],
            context_builder=context_builder,
            tool_registry=registry,
            persist_user_input=True,
            dynamic_context_sections=sections,
            context_metadata={
                "dream_job_id": job.job_id,
                "dream_signal": _signal(job),
            },
        )
        del messages
        data = _parse_json_safe(reply)
        if data is None:
            logger.warning("MojingDreamSubagentRunner JSON parse failed job={} raw={}", job.job_id, reply[:300])
            data = {
                "artifact_version": "mojing.dream_subagent.v1",
                "status": "draft",
                "summary": "DreamSubagent output was not valid JSON; raw excerpt preserved for review.",
                "memory_review": {},
                "document_review": {},
                "runtime_fact_review": {},
                "audit": {"parse_failed": True, "raw_excerpt": reply[:1200]},
                "confidence": "low",
            }
        data.setdefault("artifact_version", "mojing.dream_subagent.v1")
        data.setdefault("status", "draft")
        data.setdefault("dream_job_id", job.job_id)
        data.setdefault("runner", "mojing_dream_subagent")
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _build_tool_registry(self, job: DreamJob, ledger: Any | None, *, allow_mutation: bool) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(ReadMemoryLedgerTool(
            self._memory_ledger_repo,
            default_ledger_id=getattr(ledger, "ledger_id", None) or job.source_id,
        ))
        registry.register(ReadSessionMessagesTool(session_repo=self._session_repo, tenant_key=job.tenant_key))
        registry.register(ReadMemoryEntriesTool(db=self._db, tenant_key=job.tenant_key))
        registry.register(ReadDocumentTool(document_repo=self._document_repo, tenant_key=job.tenant_key))
        registry.register(ReadDocumentVersionsTool(document_repo=self._document_repo, tenant_key=job.tenant_key))
        if self._runtime_task_repo is not None:
            registry.register(ReadRuntimeTasksTool(runtime_task_repo=self._runtime_task_repo, tenant_key=job.tenant_key))
        registry.register(UpsertMemoryEntryTool(
            db=self._db,
            tenant_key=job.tenant_key,
            allowed=allow_mutation,
            job_id=job.job_id,
            default_source=getattr(ledger, "source", None) or "main",
            skin_apply_allowed=True,
        ))
        if allow_mutation:
            registry.register(WriteDocumentTool(
                document_repo=self._document_repo,
                tenant_key=job.tenant_key,
                allowed=True,
                job_id=job.job_id,
                session_key=job.session_key,
                trace_id=job.trace_id,
                message_seq_start=getattr(ledger, "message_seq_start", None),
                message_seq_end=getattr(ledger, "message_seq_end", None),
            ))
        return registry


def _job_packet(job: DreamJob, ledger: Any | None, *, allow_mutation: bool = False) -> dict[str, Any]:
    payload = dict(job.payload or {})
    signal = _signal(job)
    write_assets = list(payload.get("write_assets") or [])
    if allow_mutation:
        for asset in ("memory_entries", "tenant_documents"):
            if asset not in write_assets:
                write_assets.append(asset)
    elif "memory_entries" not in write_assets:
        write_assets.append("memory_entries")  # 仅供 memory_type=skin 的 upsert
    return {
        "dream_job": {
            "job_id": job.job_id,
            "candidate_id": job.candidate_id,
            "tenant_key": job.tenant_key,
            "session_key": job.session_key,
            "namespace": job.namespace,
            "trigger": job.trigger,
            "reason": job.reason,
            "source_id": job.source_id,
            "input_cursor": job.input_cursor,
            "trace_id": job.trace_id,
        },
        "dream_signal": signal,
        "asset_policy": {
            "read_assets": list(payload.get("read_assets") or []),
            "write_assets": write_assets,
            "forbidden_assets": list(payload.get("forbidden_assets") or []),
        },
        "mutation_policy": {
            "enabled": allow_mutation,
            "mode": "test_or_explicit_env" if allow_mutation else "skin_only",
            "allowed_tools": (
                ["upsert_memory_entry", "write_document"]
                if allow_mutation
                else ["upsert_memory_entry"]
            ),
            "rules": [
                "Use write tools only after reading concrete evidence.",
                "When mutation is not fully enabled, you may ONLY upsert memory entries with memory_type='skin'.",
                "Never modify forbidden assets or non-skin source='main' entries.",
                "When uncertain, produce draft suggestions instead of applying changes.",
            ],
        },
        "source_ledger": _ledger_summary(ledger) if ledger is not None else None,
        "recommended_first_tools": _recommended_tools(job, ledger),
    }


def _recommended_tools(job: DreamJob, ledger: Any | None) -> list[dict[str, Any]]:
    tools = []
    if ledger is not None:
        tools.append({"tool": "read_memory_ledger", "arguments": {"ledger_id": ledger.ledger_id}})
        tools.append({
            "tool": "read_session_messages",
            "arguments": {
                "session_key": ledger.session_key,
                "start": ledger.message_seq_start,
                "end": ledger.message_seq_end,
            },
        })
    elif job.session_key:
        tools.append({"tool": "read_session_messages", "arguments": {"session_key": job.session_key}})
    tools.extend([
        {"tool": "read_memory_entries", "arguments": {"source": "main", "top_k": 20}},
        {"tool": "read_document", "arguments": {"doc_name": "USER.md"}},
        {"tool": "read_document", "arguments": {"doc_name": "SOUL.md"}},
        {"tool": "read_runtime_tasks", "arguments": {"limit": 20}},
    ])
    return tools


def _ledger_summary(ledger: Any) -> dict[str, Any]:
    return {
        "ledger_id": ledger.ledger_id,
        "session_key": ledger.session_key,
        "source": ledger.source,
        "status": ledger.status,
        "dream_status": ledger.dream_status,
        "message_seq_start": ledger.message_seq_start,
        "message_seq_end": ledger.message_seq_end,
        "input_cursor": ledger.input_cursor,
        "tokens_before": ledger.tokens_before,
        "tokens_after": ledger.tokens_after,
        "dropped_count": ledger.dropped_count,
        "memory_actions_count": len(ledger.memory_actions or []),
    }


def _artifact_key(job: DreamJob, ledger: Any | None) -> str:
    if ledger is not None:
        return f"memory-ledger:{ledger.ledger_id}"
    signal = _signal(job)
    signal_id = str(signal.get("signal_id") or "").strip()
    if signal_id:
        return f"dream-signal:{signal_id}"
    return f"dream-job:{job.job_id}"


def _artifact_metadata(job: DreamJob, ledger: Any | None) -> dict[str, Any]:
    signal = _signal(job)
    refs: dict[str, Any] = {
        "dream_job_id": job.job_id,
        "dream_candidate_id": job.candidate_id,
        "signal_id": signal.get("signal_id"),
        "signal_type": signal.get("signal_type"),
        "source_id": job.source_id,
        "input_cursor": job.input_cursor,
    }
    if ledger is not None:
        refs["memory_ledger_ids"] = [ledger.ledger_id]
        refs["message_range"] = {
            "start": ledger.message_seq_start,
            "end": ledger.message_seq_end,
            "input_cursor": ledger.input_cursor,
        }
    return {
        "tenant_key": job.tenant_key,
        "session_key": job.session_key,
        "namespace": job.namespace,
        "trigger": job.trigger,
        "runner": "mojing_dream_subagent",
        "source_refs": refs,
    }


def _fallback_artifact(job: DreamJob, packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_version": "mojing.dream_subagent.v1",
        "status": "draft",
        "summary": "DreamSubagent captured job facts for later review; no LLM runner configured.",
        "memory_review": {
            "missed_facts": [],
            "weak_topics": [],
            "merge_suggestions": [],
            "delete_suggestions": [],
        },
        "document_review": {
            "user_md_suggestions": [],
            "soul_md_suggestions": [],
        },
        "runtime_fact_review": {
            "important_task_facts": [],
            "conflicts": [],
        },
        "audit": {
            "evidence_read": [],
            "limitations": ["llm_not_configured"],
            "packet": packet,
        },
        "confidence": "low",
        "dream_job_id": job.job_id,
        "runner": "mojing_dream_subagent",
    }


def _signal(job: DreamJob) -> dict[str, Any]:
    signal = (job.payload or {}).get("signal")
    return dict(signal) if isinstance(signal, dict) else {}


def _summary(job: DreamJob, ledger: Any | None) -> str:
    if ledger is not None:
        return f"dream subagent reviewed memory ledger {ledger.ledger_id}"
    signal_type = _signal(job).get("signal_type") or job.trigger
    return f"dream subagent reviewed signal {signal_type}"


def _parse_json_safe(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            value = json.loads(raw[start:end])
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _default_prompt() -> str:
    return (
        "You are Mojing DreamSubagent, a background reviewer for agent runtime state. "
        "Inspect evidence with tools when needed. Produce strict JSON only. "
        "Do not directly apply memory, document, or business result changes."
    )
