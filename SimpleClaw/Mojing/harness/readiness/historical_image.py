"""Readiness checks for historical image fetches."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from Mojing.harness.readiness.base import CapabilityDecision, stringify_time

if TYPE_CHECKING:
    from Mojing.storage.image_repo import ImageRepository


class HistoricalImageReadiness:
    """Computes whether a tenant has a usable historical image."""

    def __init__(self, *, image_repo: "ImageRepository | None" = None) -> None:
        self._image_repo = image_repo

    async def check_historical_image(
        self,
        tenant_key: str,
        *,
        exclude_refs: list[str] | None = None,
    ) -> CapabilityDecision:
        tenant_key = str(tenant_key or "").strip()
        refs = [str(ref).strip() for ref in (exclude_refs or []) if str(ref or "").strip()]
        facts: dict[str, Any] = {
            "tenant_key": tenant_key,
            "excluded_current_turn": bool(refs),
        }
        if not tenant_key or tenant_key == "__default__":
            return CapabilityDecision(
                allowed=False,
                capability="historical_image",
                reason="missing_tenant",
                phase="prerequisite_missing",
                message_focus=(
                    "当前无法定位用户的历史图片。请不要声称已经看过图片，"
                    "直接正常聊天；如果用户需要看图判断，再温柔引导她重新上传。"
                ),
                facts=facts,
            )

        if self._image_repo is None:
            return CapabilityDecision(
                allowed=False,
                capability="historical_image",
                reason="image_repo_unavailable",
                phase="prerequisite_missing",
                message_focus=(
                    "当前无法查询历史图片。请不要声称已经看过图片，"
                    "先根据已知画像和当前对话正常回答。"
                ),
                facts=facts,
            )

        record = await self._image_repo.get_latest_succeeded_record_excluding(
            tenant_key,
            exclude_refs=refs,
        )
        facts["latest_image_record"] = _summarize_image_record(record)
        if record:
            return CapabilityDecision(
                allowed=True,
                capability="historical_image",
                reason="ready",
                phase="ready",
                message_focus="历史图片可用，可以调用 retrieve_evidence，并指定 route=historical_image。",
                facts=facts,
            )

        if refs:
            return CapabilityDecision(
                allowed=False,
                capability="historical_image",
                reason="no_previous_image",
                phase="prerequisite_missing",
                message_focus=(
                    "本轮用户已有新上传图片，但没有更早的历史图片可供对比。"
                    "请直接基于本轮图片正常回答；不要声称已经看到了历史对比图。"
                ),
                facts=facts,
            )

        return CapabilityDecision(
            allowed=False,
            capability="historical_image",
            reason="no_history_image",
            phase="prerequisite_missing",
            message_focus=(
                "当前没有可用历史照片。请正常聊天；如果用户需要你看图判断，"
                "再温柔引导她上传一张清晰照片。"
            ),
            facts=facts,
        )


def _summarize_image_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    return {
        "job_id": record.get("job_id"),
        "image_id": record.get("image_id"),
        "status": record.get("status"),
        "created_at": stringify_time(record.get("created_at")),
        "updated_at": stringify_time(record.get("updated_at")),
    }
