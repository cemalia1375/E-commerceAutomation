"""Obligation-dispatched runtime task facts for the main agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from simpleclaw.context.providers import AttentionPacket, ContextBuildContext

from Mojing.harness.readiness.base import ACTIVE_STATUSES, normalize_status


_ACTION_FACTS = {
    "deep_report.handoff": {
        "label": "深度分析报告",
        "avoid": "不要重复调用 `deep_report_chat`",
    },
    "skin_diary.handoff": {
        "label": "肌肤日记",
        "avoid": "不要重复触发肌肤日记生成",
    },
}


@dataclass(slots=True)
class ObligationRuntimeTaskAttentionProvider:
    """Inject active background obligations so the main agent does not duplicate them."""

    runtime_task_repo: Any
    source: str = "obligation_runtime_task"
    priority: int = 20
    placement: str = "after_history"
    recent_limit: int = 80

    async def collect_attention(
        self,
        ctx: ContextBuildContext,
    ) -> list[AttentionPacket]:
        tenant_key = str(ctx.tenant_key or "").strip()
        if not tenant_key:
            return []

        tasks = await self._recent_tasks(tenant_key)
        active_by_action: dict[str, dict[str, Any]] = {}
        for task in tasks:
            payload = dict(task.get("payload") or {})
            action_key = str(payload.get("action_key") or "").strip()
            if action_key not in _ACTION_FACTS:
                continue
            if str(payload.get("source") or "").strip() != "obligation":
                continue
            if str(task.get("service_role") or "").strip() != "mojing:obligation-dispatch":
                continue
            status = normalize_status(task.get("status"))
            if status not in ACTIVE_STATUSES:
                continue
            active_by_action.setdefault(action_key, task)

        if not active_by_action:
            return []

        lines = ["【后台待办状态】"]
        metadata: dict[str, Any] = {"tasks": []}
        for action_key, task in active_by_action.items():
            fact = _ACTION_FACTS[action_key]
            task_id = str(task.get("task_id") or "").strip()
            status = normalize_status(task.get("status"))
            lines.append(
                f"用户之前要求或你之前承诺的{fact['label']}已经在后台触发，当前状态={status}。"
                f"{fact['avoid']}；如果用户问进度，说明已经在处理中，完成或失败后会提醒。"
            )
            metadata["tasks"].append({
                "action_key": action_key,
                "task_id": task_id,
                "status": status,
            })

        return [AttentionPacket(
            content="".join(lines),
            source=self.source,
            priority=self.priority,
            lifetime="one_turn",
            placement=self.placement,
            metadata=metadata,
        )]

    async def _recent_tasks(self, tenant_key: str) -> list[dict[str, Any]]:
        try:
            if hasattr(self.runtime_task_repo, "list_active_obligation_tasks"):
                return await self.runtime_task_repo.list_active_obligation_tasks(
                    tenant_key=tenant_key,
                    action_keys=tuple(_ACTION_FACTS.keys()),
                    limit=self.recent_limit,
                )
            return await self.runtime_task_repo.list_recent(
                tenant_key=tenant_key,
                limit=self.recent_limit,
            )
        except Exception:
            return []
