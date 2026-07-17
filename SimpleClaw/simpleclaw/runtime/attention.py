"""Attention provider for RuntimeTask status changes."""

from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger

from simpleclaw.context.providers import AttentionPacket, ContextBuildContext
from simpleclaw.runtime.task_protocol import RuntimeTaskRecord, RuntimeTaskStatus
from simpleclaw.runtime.task_state import RuntimeTaskStore


@dataclass(slots=True)
class RuntimeTaskAttentionProvider:
    """Surface important runtime task state changes to the next ReAct iteration."""

    store: RuntimeTaskStore
    max_items: int = 5
    statuses: set[RuntimeTaskStatus] = field(
        default_factory=lambda: {"wait_external", "succeeded", "failed"}
    )
    source: str = "runtime_task_delta"
    priority: int = 85

    async def collect_attention(self, ctx: ContextBuildContext) -> list[AttentionPacket]:
        try:
            records = await self.store.list_recent_updates(
                tenant_key=_optional_filter(ctx.tenant_key),
                session_key=_optional_filter(ctx.cache_session_key),
                limit=max(1, self.max_items * 2),
            )
        except AttributeError:
            return []
        except Exception as exc:
            logger.warning("RuntimeTaskAttentionProvider.list_recent_updates failed: {}", exc)
            return []

        important = [
            record
            for record in records
            if record.status in self.statuses
        ][: max(1, self.max_items)]
        if not important:
            return []

        return [
            AttentionPacket(
                content=_render_runtime_task_notice(important),
                source=self.source,
                priority=self.priority,
                lifetime="until_changed",
                placement="before_last_user",
                metadata={
                    "runtime_tasks": [_record_signature(record) for record in important],
                },
            )
        ]


def _render_runtime_task_notice(records: list[RuntimeTaskRecord]) -> str:
    lines = ["后台任务状态更新："]
    lines.extend(_record_line(record) for record in records)
    return "\n".join(lines)


def _record_line(record: RuntimeTaskRecord) -> str:
    label = record.tool_name or record.task_type or "runtime_task"
    task_id = record.task_id
    if record.status == "succeeded":
        ref = _business_ref(record)
        suffix = f"，{ref}" if ref else ""
        return f"- {label}({task_id}) 已完成{suffix}。{_summary(record)}".rstrip()
    if record.status == "failed":
        error = record.error or record.summary or "unknown error"
        return f"- {label}({task_id}) 失败：{error}"
    if record.status == "wait_external":
        external = f"，external_job_id={record.external_job_id}" if record.external_job_id else ""
        return f"- {label}({task_id}) 正在等待外部系统完成{external}。{_summary(record)}".rstrip()
    return f"- {label}({task_id}) 状态：{record.status}。{_summary(record)}".rstrip()


def _summary(record: RuntimeTaskRecord) -> str:
    return str(record.summary or "").strip()


def _business_ref(record: RuntimeTaskRecord) -> str:
    if record.business_ref_type and record.business_ref_id:
        return f"{record.business_ref_type}={record.business_ref_id}"
    if record.business_ref_id:
        return f"business_ref_id={record.business_ref_id}"
    return ""


def _record_signature(record: RuntimeTaskRecord) -> dict:
    return {
        "task_id": record.task_id,
        "status": record.status,
        "updated_at_ms": record.updated_at_ms,
        "business_ref_type": record.business_ref_type,
        "business_ref_id": record.business_ref_id,
        "external_job_id": record.external_job_id,
        "error": record.error,
    }


def _optional_filter(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text or text == "__default__":
        return None
    return text
