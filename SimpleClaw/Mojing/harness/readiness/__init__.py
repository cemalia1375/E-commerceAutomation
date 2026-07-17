"""Readiness services for Mojing tool capabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from Mojing.harness.readiness.base import CapabilityDecision
from Mojing.harness.readiness.deep_report import DeepReportReadiness
from Mojing.harness.readiness.historical_image import HistoricalImageReadiness
from Mojing.harness.readiness.image_analysis import ImageAnalysisReadiness, ImageAnalysisStatus
from Mojing.harness.readiness.skin_diary import SkinDiaryGenerationReadiness

if TYPE_CHECKING:
    from Mojing.storage.deep_report_repo import DeepReportRepository
    from Mojing.storage.document_repo import DocumentRepository
    from Mojing.storage.image_repo import ImageRepository
    from Mojing.storage.runtime_task_repo import RuntimeTaskRepository
    from Mojing.storage.skin_profile_repo import SkinProfileRepository


class CapabilityReadinessService:
    """Compatibility facade over split capability readiness services.

    New code should inject the specific readiness service it needs:
    DeepReportReadiness or HistoricalImageReadiness.
    """

    def __init__(
        self,
        *,
        document_repo: "DocumentRepository | None" = None,
        image_repo: "ImageRepository | None" = None,
        skin_profile_repo: "SkinProfileRepository | None" = None,
        runtime_task_repo: "RuntimeTaskRepository | None" = None,
        deep_report_repo: "DeepReportRepository | None" = None,
        timezone_name: str = "Asia/Shanghai",
    ) -> None:
        self.deep_report = DeepReportReadiness(
            document_repo=document_repo,
            image_repo=image_repo,
            image_analysis_readiness=ImageAnalysisReadiness(
                image_repo=image_repo,
                document_repo=document_repo,
                runtime_task_repo=runtime_task_repo,
                skin_profile_repo=skin_profile_repo,
                timezone_name=timezone_name,
            ),
            runtime_task_repo=runtime_task_repo,
            deep_report_repo=deep_report_repo,
            timezone_name=timezone_name,
        )
        self.historical_image = HistoricalImageReadiness(image_repo=image_repo)

    async def check_deep_report(self, tenant_key: str) -> CapabilityDecision:
        return await self.deep_report.check_deep_report(tenant_key)

    async def check_historical_image(
        self,
        tenant_key: str,
        *,
        exclude_refs: list[str] | None = None,
    ) -> CapabilityDecision:
        return await self.historical_image.check_historical_image(
            tenant_key,
            exclude_refs=exclude_refs,
        )


__all__ = [
    "CapabilityDecision",
    "CapabilityReadinessService",
    "DeepReportReadiness",
    "HistoricalImageReadiness",
    "ImageAnalysisReadiness",
    "ImageAnalysisStatus",
    "SkinDiaryGenerationReadiness",
]
