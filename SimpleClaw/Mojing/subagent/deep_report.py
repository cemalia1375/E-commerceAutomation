"""DeepReportSubagent — 深度分析报告子 Agent."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from simpleclaw.context.builder import ContextBuilder
from simpleclaw.context.providers import ContextBuildContext, ContextSection
from simpleclaw.harness.hooks import PostrunHook
from simpleclaw.subagent.base import SubagentBase
from simpleclaw.tools.registry import ToolRegistry

from Mojing.context.deep_report import (
    DeepReportContextProvider,
    DeepReportHandoffContractAttentionProvider,
    deep_report_document_provider,
)
from Mojing.skills import get_deep_report_skill_registry
from Mojing.storage.database import Database
from Mojing.storage.deep_report_repo import DeepReportRepository
from Mojing.storage.document_repo import DocumentRepository
from Mojing.storage.memory_repo import MySQLMemory
from Mojing.storage.obligation_repo import ObligationRepository
from Mojing.storage.session_repo import SessionRepository

if TYPE_CHECKING:
    from simpleclaw.llm.base import LLMProvider
    from simpleclaw.runtime.side_effects import PostTurnEffects
    from Mojing.storage.image_repo import ImageRepository
    from Mojing.storage.runtime_task_repo import RuntimeTaskRepository
    from Mojing.storage.skin_profile_repo import SkinProfileRepository

_PROMPT_PATH = Path(__file__).parent / "prompt" / "deep_report.md"


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8").strip()


class DeepReportSubagent(SubagentBase):
    """深度报告子 Agent。

    组装层只声明五组能力：
      - Stable prompt: deep_report.md + workspace/SOUL.md + compliance.md
      - Dynamic context: USER/SOUL + 当前/指定深度报告
      - Attention: evidence and runtime state providers
      - Tools: deep_research / check_runtime_status / retrieve_evidence
      - Post-turn: deep_report_postprocess / cold_path
    """

    name = "deep_report"

    def __init__(
        self,
        db: Database,
        document_repo: DocumentRepository,
        llm: "LLMProvider",
        hook_llm: "LLMProvider",
        obligation_repo: ObligationRepository,
        session_repo: SessionRepository,
        llm_cache_repo: object | None = None,
        endpoint_url: str = "",
        runtime_task_repo: "RuntimeTaskRepository | None" = None,
        image_repo: "ImageRepository | None" = None,
        skin_profile_repo: "SkinProfileRepository | None" = None,
        post_turn_effects: "PostTurnEffects | None" = None,
    ) -> None:
        self._db = db
        self._document_repo = document_repo
        self._llm = llm
        self._report_repo = DeepReportRepository(db)
        self._endpoint_url = endpoint_url
        self._runtime_task_repo = runtime_task_repo
        self._image_repo = image_repo
        self._skin_profile_repo = skin_profile_repo
        self._prompt = _load_prompt()
        self._skill_registry = get_deep_report_skill_registry()

        from Mojing.config import load_compliance, load_workspace_section
        self._workspace_soul = load_workspace_section("SOUL.md")
        self._compliance = load_compliance()

        from Mojing.agent.cold_path import ColdPathHook
        from Mojing.subagent.deep_report_postprocess import DeepReportPostprocessHook

        self._postprocess_hook: PostrunHook = DeepReportPostprocessHook(
            llm=hook_llm,
            document_repo=document_repo,
        )
        self._cold_path_hook: PostrunHook = ColdPathHook(
            llm=hook_llm,
            obligation_repo=obligation_repo,
            cache_repo=llm_cache_repo,
        )
        self._post_turn_effects = post_turn_effects
        del session_repo

    def session_key_for(self, tenant_key: str) -> str:
        return f"deep_report:{tenant_key}"

    def matches(self, session_key: str) -> bool:
        return session_key.startswith("deep_report:")

    async def make_context_builder(self, tenant_key: str) -> ContextBuilder:
        stable = [self._prompt]
        if self._workspace_soul:
            stable.append(self._workspace_soul)
        if self._compliance:
            stable.append(self._compliance)
        return ContextBuilder(
            stable_sections=stable,
            stable_prompt_providers=self.make_stable_prompt_providers(tenant_key),
            dynamic_context_providers=[],
            attention_providers=self.make_attention_providers(tenant_key),
            skill_registry=self._skill_registry,
            include_skill_index=True,
            tenant_key=tenant_key,
            cache_lane="deep_report",
            cache_session_key=self.session_key_for(tenant_key),
        )

    def make_dynamic_context_providers(self, tenant_key: str):
        del tenant_key
        return [
            deep_report_document_provider(self._document_repo),
            DeepReportContextProvider(
                report_repo=self._report_repo,
                runtime_task_repo=self._runtime_task_repo,
            ),
        ]

    async def fetch_dynamic_context_sections(
        self,
        tenant_key: str,
        *,
        message: str = "",
        media: list[str] | None = None,
        report_id: str | None = None,
    ) -> list[ContextSection]:
        ctx = ContextBuildContext(
            history=[],
            query=message,
            tenant_key=tenant_key,
            cache_lane="deep_report",
            cache_session_key=self.session_key_for(tenant_key),
            metadata={
                "report_id": report_id or "",
                "media": media or [],
            },
        )
        sections: list[ContextSection] = []
        for provider in self.make_dynamic_context_providers(tenant_key):
            sections.extend(await provider.collect_dynamic_context(ctx))
        return sections

    def make_attention_providers(self, tenant_key: str):
        del tenant_key
        return [
            DeepReportHandoffContractAttentionProvider(),
        ]

    def make_tool_registry(self, tenant_key: str) -> ToolRegistry:
        from Mojing.harness.readiness import (
            DeepReportReadiness,
            HistoricalImageReadiness,
            ImageAnalysisReadiness,
        )
        from Mojing.harness.tool_gates import DeepReportGate, HistoricalImageGate
        from Mojing.tools.deep_research import DeepResearchTool
        from Mojing.tools.retrieve_evidence import RetrieveEvidenceTool
        from Mojing.tools.runtime_status import CheckRuntimeStatusTool
        from simpleclaw.harness.lifecycle import ToolLifecycle

        image_analysis_readiness = ImageAnalysisReadiness(
            image_repo=self._image_repo,
            document_repo=self._document_repo,
            runtime_task_repo=self._runtime_task_repo,
            skin_profile_repo=self._skin_profile_repo,
        )
        deep_report_readiness = DeepReportReadiness(
            document_repo=self._document_repo,
            image_repo=self._image_repo,
            skin_profile_repo=self._skin_profile_repo,
            image_analysis_readiness=image_analysis_readiness,
            runtime_task_repo=self._runtime_task_repo,
            deep_report_repo=self._report_repo,
        )

        before_tool_hooks = [DeepReportGate(deep_report_readiness)]
        if self._image_repo is not None:
            before_tool_hooks.append(
                HistoricalImageGate(HistoricalImageReadiness(image_repo=self._image_repo)),
            )

        registry = ToolRegistry(
            tool_lifecycle=ToolLifecycle(before_tool_hooks=before_tool_hooks),
        )
        registry.register(
            DeepResearchTool(
                endpoint_url=self._endpoint_url,
                document_repo=self._document_repo,
                image_repo=self._image_repo,
                runtime_task_repo=self._runtime_task_repo,
            )
        )
        if self._runtime_task_repo is not None:
            registry.register(CheckRuntimeStatusTool(
                runtime_task_repo=self._runtime_task_repo,
                image_repo=self._image_repo,
                include_deep_report=True,
                include_skin_diary=False,
            ))
        if self._image_repo is not None:
            registry.register(RetrieveEvidenceTool(
                llm=self._llm,
                memory=MySQLMemory(db=self._db, tenant_key=tenant_key, source="deep_report"),
                image_repo=self._image_repo,
            ))
        return registry

    def make_postprocess_hook(self) -> PostrunHook:
        return self._postprocess_hook

    def make_cold_path_hook(self) -> PostrunHook:
        return self._cold_path_hook

    def make_post_turn_effects(self) -> "PostTurnEffects | None":
        return self._post_turn_effects
