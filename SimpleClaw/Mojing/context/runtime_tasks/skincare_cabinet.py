"""护肤柜产品调研任务的异步事实注入 provider。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from simpleclaw.context.providers import AttentionPacket, ContextBuildContext
from Mojing.runtime.task_types import MojingTaskType


def _normalize_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"triggered", "external", "waiting_external"}:
        return "wait_external"
    return text or "queued"


def _tenant_user_id(tenant_key: str) -> str | None:
    text = str(tenant_key or "").strip()
    if not text:
        return None
    return text


@dataclass(slots=True)
class SkincareCabinetResearchRuntimeTaskAttentionProvider:
    """Inject sparse facts for cabinet product research task changes.

    只做两件事：
    1. 调研失败 -> 提醒模型可重试，但不要假装已经查到了
    2. 调研成功且产品仍未落柜 -> 提醒模型可询问用户是否要加入护肤柜
    """

    runtime_task_repo: Any
    cabinet_repo: Any
    emission_state: dict[str, Any]
    source: str = "skincare_cabinet_runtime_task"
    priority: int = 17
    placement: str = "after_history"

    async def collect_attention(
        self,
        ctx: ContextBuildContext,
    ) -> list[AttentionPacket]:
        tenant_key = str(ctx.tenant_key or "").strip()
        if not tenant_key:
            return []

        latest = await self._latest_task(tenant_key)
        if not latest:
            self._clear_state(tenant_key)
            return []

        product = await self._linked_product(latest, tenant_key)
        status = _normalize_status(latest.get("status"))
        in_cabinet = int(product.get("in_cabinet") or 0) if product else -1
        record_task = await self._linked_record_task(latest, tenant_key)
        if _record_task_blocks_confirmation(record_task):
            return []
        signature = ":".join([
            str(latest.get("task_id") or ""),
            status,
            str(latest.get("business_ref_id") or ""),
            str(in_cabinet),
        ])
        if not self._should_emit(tenant_key, signature):
            return []

        content = _build_attention_content(latest=latest, product=product, status=status)
        if not content:
            return []
        return [AttentionPacket(
            content=content,
            source=self.source,
            priority=self.priority,
            lifetime="one_turn",
            placement=self.placement,
            metadata={
                "task_id": str(latest.get("task_id") or ""),
                "status": status,
                "product_id": str(latest.get("business_ref_id") or ""),
                "in_cabinet": None if product is None else int(product.get("in_cabinet") or 0),
            },
        )]

    async def _latest_task(self, tenant_key: str) -> dict[str, Any] | None:
        try:
            return await self.runtime_task_repo.find_latest_task_for(
                tenant_key=tenant_key,
                task_type=MojingTaskType.CABINET_PRODUCT_RESEARCH,
            )
        except Exception:
            return None

    async def _linked_product(self, task: dict[str, Any], tenant_key: str) -> dict[str, Any] | None:
        product_id_text = str(task.get("business_ref_id") or "").strip()
        if not product_id_text.isdigit():
            return None
        user_id = _tenant_user_id(tenant_key)
        if user_id is None:
            return None
        try:
            return await self.cabinet_repo.get(product_id=int(product_id_text), user_id=user_id)
        except Exception:
            return None

    async def _linked_record_task(self, task: dict[str, Any], tenant_key: str) -> dict[str, Any] | None:
        finder = getattr(self.runtime_task_repo, "find_latest_by_source_task_id", None)
        if not callable(finder):
            return None
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            return None
        try:
            return await finder(
                tenant_key=tenant_key,
                task_type=MojingTaskType.CABINET_PRODUCT_RECORD,
                source_task_id=task_id,
            )
        except Exception:
            return None

    def _should_emit(self, tenant_key: str, signature: str) -> bool:
        key = f"{tenant_key}:{self.source}:signature"
        previous = self.emission_state.get(key)
        if previous == signature:
            return False
        self.emission_state[key] = signature
        return True

    def _clear_state(self, tenant_key: str) -> None:
        self.emission_state.pop(f"{tenant_key}:{self.source}:signature", None)


def _record_task_blocks_confirmation(task: dict[str, Any] | None) -> bool:
    if not task:
        return False
    return _normalize_status(task.get("status")) in {"queued", "running", "wait_external", "succeeded"}


def _build_attention_content(
    *,
    latest: dict[str, Any],
    product: dict[str, Any] | None,
    status: str,
) -> str:
    if status == "failed":
        return (
            "【护肤柜调研状态】最近一次产品资料调研失败了。"
            "如果当前用户还想继续查这款产品，可以自然询问是否要重试；"
            "不要假装你已经拿到了这款产品的完整资料。"
        )

    if status != "succeeded" or not product:
        return ""

    if int(product.get("in_cabinet") or 0) != 0:
        return ""

    brand = str(product.get("brand") or "").strip()
    product_name = str(product.get("product_name") or "").strip()
    product_id = int(product.get("id") or 0)
    return (
        f"【护肤柜调研状态】产品资料已经整理完成：{brand} {product_name}。"
        "这条记录当前还是未落柜状态。"
        "如果当前还在聊这款产品，可以自然询问用户是否要加入护肤柜；"
        f"如果用户明确同意，请调用 confirm_skincare_cabinet_record(product_id={product_id})。"
    )
