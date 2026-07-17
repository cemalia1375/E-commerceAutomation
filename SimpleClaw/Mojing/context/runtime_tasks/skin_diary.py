"""肌肤日记 handoff 路由事实的异步状态注入 provider。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from simpleclaw.context.providers import AttentionPacket, ContextBuildContext

from Mojing.harness.readiness.base import ACTIVE_STATUSES, normalize_status
from Mojing.runtime.task_types import MojingTaskType


@dataclass(slots=True)
class SkinDiaryHandoffRuntimeTaskAttentionProvider:
    """Inject concise route facts after image analysis completes."""

    runtime_task_repo: Any
    action_usage_repo: Any
    emission_state: dict[str, Any]
    source: str = "skin_diary_runtime_task"
    priority: int = 19
    placement: str = "after_history"

    async def collect_attention(
        self,
        ctx: ContextBuildContext,
    ) -> list[AttentionPacket]:
        tenant_key = str(ctx.tenant_key or "").strip()
        if not tenant_key:
            return []

        latest = await self._latest_image_analysis_task(tenant_key)
        if not latest:
            self._clear_state(tenant_key)
            return []

        status = normalize_status(latest.get("status"))
        if status != "succeeded":
            self._clear_state(tenant_key)
            return []

        counts = await self._handoff_counts(tenant_key)
        submitted_count = int(counts.get("submitted_count") or 0)
        latest_diary = await self._latest_skin_diary_generation_task(tenant_key)
        latest_dispatch = await self._latest_skin_diary_dispatch_task(tenant_key)
        if (
            _task_covers_image_analysis(latest_diary, latest)
            or _auto_dispatch_covers_image_analysis(latest_dispatch, latest)
        ):
            return []

        signature = ":".join([
            str(latest.get("task_id") or ""),
            status,
            str(latest.get("completed_at") or latest.get("updated_at") or ""),
            str(submitted_count),
        ])

        if not self._should_emit(
            tenant_key,
            signature=signature,
        ):
            return []

        return [AttentionPacket(
            content=_build_skin_diary_route_fact(submitted_count=submitted_count),
            source=self.source,
            priority=self.priority,
            lifetime="one_turn",
            placement=self.placement,
            metadata={
                "task_id": str(latest.get("task_id") or ""),
                "status": status,
                "submitted_count": submitted_count,
            },
        )]

    async def _latest_image_analysis_task(self, tenant_key: str) -> dict[str, Any] | None:
        try:
            return await self.runtime_task_repo.find_latest_task_for(
                tenant_key=tenant_key,
                task_type=MojingTaskType.IMAGE_ANALYSIS,
            )
        except Exception:
            return None

    async def _latest_skin_diary_generation_task(self, tenant_key: str) -> dict[str, Any] | None:
        try:
            return await self.runtime_task_repo.find_latest_task_for(
                tenant_key=tenant_key,
                task_type=MojingTaskType.SKIN_DIARY_GENERATION,
            )
        except Exception:
            return None

    async def _latest_skin_diary_dispatch_task(self, tenant_key: str) -> dict[str, Any] | None:
        try:
            return await self.runtime_task_repo.find_latest_task_for(
                tenant_key=tenant_key,
                task_type=MojingTaskType.SUBAGENT_DISPATCH,
            )
        except Exception:
            return None

    async def _handoff_counts(self, tenant_key: str) -> dict[str, int]:
        try:
            return await self.action_usage_repo.get_counts(tenant_key, "skin_diary.handoff")
        except Exception:
            return {
                "submitted_count": 0,
                "succeeded_count": 0,
                "failed_count": 0,
            }

    def _should_emit(self, tenant_key: str, *, signature: str) -> bool:
        key = f"{tenant_key}:{self.source}:signature"
        previous = self.emission_state.get(key)
        if previous != signature:
            self.emission_state[key] = signature
            return True
        return False

    def _clear_state(self, tenant_key: str) -> None:
        self.emission_state.pop(f"{tenant_key}:{self.source}:signature", None)


def _build_skin_diary_route_fact(*, submitted_count: int) -> str:
    if submitted_count <= 0:
        return (
            "【肌肤日记路由事实】图片分析工具已完成。"
            "首次肌肤日记应由系统在 USER.md 同步完成后自动生成；"
            "当前尚未看到肌肤日记派发记录。"
            "不要自行触发肌肤日记。"
            "如果用户问进度，先调用 `check_runtime_status(target=\"skin_diary\")`。"
        )
    return (
        "【肌肤日记路由事实】图片分析工具已完成。"
        f"用户已使用过肌肤日记（skin_diary.handoff.submitted_count={submitted_count}）。"
        "如需更新肌肤日记，先使用 `skin_diary.offer_refresh`。"
        "触发前必须先回应用户当前问题，不要跳过当前问题直接刷新。"
    )


def _task_covers_image_analysis(
    related_task: dict[str, Any] | None,
    image_task: dict[str, Any] | None,
    *,
    allow_before_seconds: int = 0,
) -> bool:
    """Return True when a related task already handles this image result."""
    if not related_task or not image_task:
        return False
    status = normalize_status(related_task.get("status"))
    if status not in ACTIVE_STATUSES and status != "succeeded":
        return False
    image_time = _task_time(image_task, "completed_at", "updated_at", "created_at")
    related_time = _task_time(related_task, "created_at", "updated_at", "completed_at")
    if image_time is None or related_time is None:
        return False
    if related_time >= image_time:
        return True
    if allow_before_seconds <= 0:
        return False
    return (image_time - related_time).total_seconds() <= allow_before_seconds


def _auto_dispatch_covers_image_analysis(
    dispatch_task: dict[str, Any] | None,
    image_task: dict[str, Any] | None,
) -> bool:
    payload = dict((dispatch_task or {}).get("payload") or {})
    if str(payload.get("source") or "").strip() != "skin_profile_sync":
        return False
    if str(payload.get("action_key") or "").strip() != "skin_diary.handoff":
        return False
    return _task_covers_image_analysis(
        dispatch_task,
        image_task,
        allow_before_seconds=60,
    )


def _task_time(task: dict[str, Any], *keys: str) -> datetime | None:
    for key in keys:
        parsed = _coerce_datetime(task.get(key))
        if parsed is not None:
            return parsed
    return None


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed
