"""Light Dream runner for Mojing memory ledger review."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from simpleclaw.dream.protocol import DreamArtifact, DreamJob, DreamResult
from simpleclaw.llm.base import LLMProvider
from simpleclaw.llm.chunks import TextChunk
from Mojing.storage.memory_ledger_repo import MemoryLedgerRepository


_DREAM_PROMPT_PATH = Path(__file__).parent.parent / "workspace" / "dream_light.md"


def load_dream_template() -> str:
    if _DREAM_PROMPT_PATH.exists():
        return _DREAM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return ""


class MojingLightDreamRunner:
    """Review one memory ledger and produce a draft DreamArtifact.

    P0 intentionally does not apply memory changes. It only creates a durable
    artifact that can later be evaluated, validated, and applied.
    """

    def __init__(
        self,
        *,
        memory_ledger_repo: MemoryLedgerRepository,
        llm: LLMProvider | None = None,
    ) -> None:
        self._memory_ledger_repo = memory_ledger_repo
        self._llm = llm

    async def __call__(self, job: DreamJob) -> DreamResult:
        ledger_id = str(job.source_id or "").strip()
        if not ledger_id:
            return DreamResult.skipped(job.job_id, summary="dream skipped: missing source ledger")

        ledger = await self._memory_ledger_repo.get_ledger(ledger_id)
        if ledger is None:
            return DreamResult.skipped(job.job_id, summary=f"dream skipped: ledger not found {ledger_id}")
        if ledger.status != "applied":
            await self._memory_ledger_repo.update_ledger(
                ledger_id,
                dream_status="not_needed",
                metadata={"dream_skip_reason": f"ledger status is {ledger.status}"},
            )
            return DreamResult.skipped(job.job_id, summary=f"dream skipped: ledger status={ledger.status}")

        await self._memory_ledger_repo.update_ledger(
            ledger_id,
            dream_status="candidate",
            metadata={"dream_job_id": job.job_id},
        )

        try:
            content = await self._build_artifact_content(job, ledger)
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            await self._memory_ledger_repo.update_ledger(
                ledger_id,
                dream_status="failed",
                last_error=error,
                metadata={"dream_job_id": job.job_id},
            )
            raise

        artifact = DreamArtifact(
            job_id=job.job_id,
            artifact_type="memory_summary",
            content=content,
            key=f"memory-ledger:{ledger_id}",
            status="draft",
            metadata={
                "tenant_key": ledger.tenant_key,
                "session_key": ledger.session_key,
                "source_refs": {
                    "memory_ledger_ids": [ledger_id],
                    "message_range": {
                        "start": ledger.message_seq_start,
                        "end": ledger.message_seq_end,
                    },
                    "input_cursor": ledger.input_cursor,
                },
                "dream_job_id": job.job_id,
                "source": ledger.source,
            },
        )
        await self._memory_ledger_repo.update_ledger(
            ledger_id,
            dream_status="reviewed",
            metadata={"dream_job_id": job.job_id, "dream_artifact_id": artifact.artifact_id},
        )
        return DreamResult.succeeded(
            job.job_id,
            summary=f"dream reviewed memory ledger {ledger_id}",
            artifacts=[artifact],
            metadata={"memory_ledger_id": ledger_id},
        )

    async def _build_artifact_content(self, job: DreamJob, ledger: Any) -> str:
        packet = _ledger_packet(ledger, job=job)
        if self._llm is None:
            return json.dumps(_fallback_artifact(packet), ensure_ascii=False, indent=2)

        template = load_dream_template()
        if not template:
            return json.dumps(_fallback_artifact(packet), ensure_ascii=False, indent=2)
        prompt = template.replace("<<<DREAM_PACKET>>>", json.dumps(packet, ensure_ascii=False, default=str))
        raw = await _llm_complete(self._llm, prompt, max_tokens=1400)
        data = _parse_json_safe(raw)
        if data is None:
            logger.warning("dream artifact JSON parse failed raw={}", raw[:300])
            data = {
                "artifact_version": "mojing.light_dream.v1",
                "status": "draft",
                "summary": "LLM output was not valid JSON; raw excerpt preserved for review.",
                "memory_actions": [],
                "audit": {"parse_failed": True, "raw_excerpt": raw[:1200]},
                "confidence": "low",
            }
        data.setdefault("artifact_version", "mojing.light_dream.v1")
        data.setdefault("source", "light_dream")
        data.setdefault("dream_job_id", job.job_id)
        return json.dumps(data, ensure_ascii=False, indent=2)


def _ledger_packet(ledger: Any, *, job: DreamJob | None = None) -> dict[str, Any]:
    job_payload = dict(getattr(job, "payload", None) or {})
    return {
        "ledger_id": ledger.ledger_id,
        "tenant_key": ledger.tenant_key,
        "session_key": ledger.session_key,
        "source": ledger.source,
        "trigger_type": ledger.trigger_type,
        "message_range": {
            "start": ledger.message_seq_start,
            "end": ledger.message_seq_end,
            "cursor": ledger.input_cursor,
        },
        "tokens": {
            "before": ledger.tokens_before,
            "after": ledger.tokens_after,
            "dropped_count": ledger.dropped_count,
        },
        "source_chunk": _compact(ledger.source_chunk, 80),
        "memory_before": ledger.memory_before.to_dict() if ledger.memory_before else None,
        "memory_actions": ledger.memory_actions or [],
        "memory_after": ledger.memory_after.to_dict() if ledger.memory_after else None,
        "business_snapshot": _compact(ledger.business_snapshot, 40),
        "dream_signal": job_payload.get("signal") if job_payload else None,
        "asset_policy": {
            "read_assets": list(job_payload.get("read_assets") or []),
            "write_assets": list(job_payload.get("write_assets") or []),
            "forbidden_assets": list(job_payload.get("forbidden_assets") or []),
        },
    }


def _fallback_artifact(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_version": "mojing.light_dream.v1",
        "status": "draft",
        "summary": "Light dream captured ledger facts for later review.",
        "memory_actions": [
            {
                "action": "noop",
                "reason": "No LLM runner configured; artifact preserves ledger packet for manual review.",
                "source_refs": {
                    "memory_ledger_ids": [packet.get("ledger_id")],
                    "message_range": packet.get("message_range"),
                },
            }
        ],
        "audit": {
            "missed_signals": [],
            "duplicate_topics": [],
            "weak_descriptions": [],
            "conflicts": [],
            "business_fact_notes": [],
        },
        "confidence": "low",
        "ledger_packet": packet,
    }


def _compact(value: Any, limit: int) -> Any:
    if isinstance(value, list):
        return value[:limit]
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= limit:
                compacted["_truncated"] = True
                break
            compacted[key] = item
        return compacted
    return value


async def _llm_complete(llm: LLMProvider, prompt: str, max_tokens: int) -> str:
    parts: list[str] = []
    async for chunk in llm.stream_with_retry(
        [{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.0,
    ):
        if isinstance(chunk, TextChunk):
            parts.append(chunk.token)
    return "".join(parts).strip()


def _parse_json_safe(raw: str) -> dict | None:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end])
        except json.JSONDecodeError:
            return None
    return None
