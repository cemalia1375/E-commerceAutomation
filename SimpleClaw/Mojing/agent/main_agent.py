"""MainAgent — 主 Agent 的装配层。

对称于 SubagentBase + SkinDiarySubagent：把主 Agent 的"稳定前缀 / 工具 /
动态上下文 / attention"装配职责从 SessionStore、config、server 三处收拢到这里。

SessionStore 冷启动时调用：
  - make_context_builder(tenant_key, stage) → ContextBuilder
  - make_tool_registry(tenant_key, stage)   → ToolRegistry

每次模型调用前：
  - ReactLoop → ContextBuilder.build()
  - ContextBuilder 内部调用 dynamic_context_providers / attention_providers
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from simpleclaw.context.builder import ContextBuilder
from simpleclaw.context.providers import MemoryDynamicContextProvider
from simpleclaw.tools.base import Tool
from simpleclaw.tools.registry import ToolRegistry

from Mojing.agent.capabilities import AgentCapabilities
from Mojing.config import load_stable_sections
from Mojing.context import (
    CurrentTimeContextProvider,
    DeepReportGateAttentionProvider,
    DeepReportOutcomeAttentionProvider,
    DocumentContextProvider,
    EvidenceAttentionProvider,
    ImageAnalysisCompletionAttentionProvider,
    ImageAnalysisFailureAttentionProvider,
    ImageUploadAttentionProvider,
    ObligationRuntimeTaskAttentionProvider,
    SkinDiaryCompletionAttentionProvider,
    SkinDiaryHandoffRuntimeTaskAttentionProvider,
    SkincareCabinetResearchRuntimeTaskAttentionProvider,
    SelfieAgeAttentionProvider,
    main_agent_document_specs,
)
from Mojing.skills import get_main_skill_registry
from Mojing.storage.memory_repo import MySQLMemory

if TYPE_CHECKING:
    from Mojing.storage.skincare_cabinet_repo import SkincareCabinetRepository
    from Mojing.storage.database import Database
    from Mojing.storage.document_repo import DocumentRepository
    from Mojing.storage.deep_report_repo import DeepReportRepository
    from Mojing.storage.image_repo import ImageRepository
    from Mojing.storage.runtime_task_repo import RuntimeTaskRepository
    from Mojing.storage.action_usage_repo import ActionUsageRepository
    from Mojing.storage.completion_event_repo import CompletionEventRepository
    from Mojing.storage.skin_profile_repo import SkinProfileRepository
    from Mojing.storage.tenant_state_repo import TenantStateRepository
    from simpleclaw.tools.invocation import ToolInvocationStore


class MainAgent:
    """主 Agent 的装配层。

    持有所有装配所需的依赖；SessionStore / _run_main_turn 只调它的方法，
    不需要知道 USER.md / 自拍时距 / 话题 attention / stable_sections 的具体来源。
    """

    _VALID_STAGES: tuple[str, ...] = ("novice", "explore", "mature")

    def __init__(
        self,
        *,
        db: "Database",
        document_repo: "DocumentRepository",
        image_repo: "ImageRepository",
        base_registry: ToolRegistry,
        tool_factories: list[Callable[[str], Tool]] | None = None,
        device_tool_factories: list[Callable[[str], Tool]] | None = None,
        staged_tool_factories: list[tuple[frozenset[str], Callable[[str], Tool]]] | None = None,
        runtime_task_repo: "RuntimeTaskRepository | None" = None,
        action_usage_repo: "ActionUsageRepository | None" = None,
        completion_event_repo: "CompletionEventRepository | None" = None,
        skincare_cabinet_repo: "SkincareCabinetRepository | None" = None,
        deep_report_repo: "DeepReportRepository | None" = None,
        skin_profile_repo: "SkinProfileRepository | None" = None,
        tenant_state_repo: "TenantStateRepository | None" = None,
        tool_invocation_store: "ToolInvocationStore | None" = None,
    ) -> None:
        self._db = db
        self._document_repo = document_repo
        self._image_repo = image_repo
        self._base_registry = base_registry
        self._tool_factories = tool_factories or []
        self._device_tool_factories = device_tool_factories or []
        self._staged_tool_factories = staged_tool_factories or []
        self._runtime_task_repo = runtime_task_repo
        self._action_usage_repo = action_usage_repo
        self._completion_event_repo = completion_event_repo
        self._skincare_cabinet_repo = skincare_cabinet_repo
        self._deep_report_repo = deep_report_repo
        self._skin_profile_repo = skin_profile_repo
        self._tenant_state_repo = tenant_state_repo
        self._tool_invocation_store = tool_invocation_store
        self._deep_report_gate_attention_state: dict[str, object] = {}
        self._deep_report_outcome_attention_state: dict[str, object] = {}
        self._skincare_cabinet_task_attention_state: dict[str, object] = {}
        self._skin_diary_task_attention_state: dict[str, object] = {}
        self._skin_diary_completion_attention_state: dict[str, object] = {}
        self._image_analysis_completion_attention_state: dict[str, object] = {}
        self._image_analysis_failure_attention_state: dict[str, object] = {}
        self._skill_registry = get_main_skill_registry()

        # 启动时预热所有合法阶段的 stable_sections（文件 I/O 只发生一次）
        self._stable_cache: dict[tuple[str, str], list[str]] = {
            (s, surface): load_stable_sections(stage=s, prompt_surface=surface)
            for s in self._VALID_STAGES
            for surface in ("app", "device")
        }

    # ------------------------------------------------------------------
    # 冷启动装配（SessionStore.get_or_create 调用）
    # ------------------------------------------------------------------

    async def make_context_builder(
        self,
        tenant_key: str,
        stage: str = "novice",
        capabilities: AgentCapabilities | None = None,
    ) -> ContextBuilder:
        """为指定租户 + 阶段创建 ContextBuilder。

        stable_sections 从启动时预热的缓存取；记忆通过 DynamicContextProvider 注入。
        """
        capabilities = capabilities or AgentCapabilities()
        prompt_surface = str(capabilities.prompt_surface or "app").strip().lower()
        stable = (
            self._stable_cache.get((stage, prompt_surface))
            or self._stable_cache.get(("novice", prompt_surface))
            or self._stable_cache.get((stage, "app"))
            or self._stable_cache[("novice", "app")]
        )
        return ContextBuilder(
            stable_sections=stable,
            stable_prompt_providers=self.make_stable_prompt_providers(tenant_key),
            dynamic_context_providers=self.make_dynamic_context_providers(tenant_key),
            attention_providers=self.make_attention_providers(tenant_key),
            skill_registry=self._skill_registry,
            include_skill_index=True,
            tenant_key=tenant_key,
            cache_lane="main_agent",
        )

    def make_stable_prompt_providers(self, tenant_key: str):
        del tenant_key
        return []

    def make_dynamic_context_providers(self, tenant_key: str):
        memory = MySQLMemory(db=self._db, tenant_key=tenant_key, source="main")
        return [
            *self._base_dynamic_context_providers(),
            MemoryDynamicContextProvider(memory),
        ]

    def make_attention_providers(self, tenant_key: str):
        del tenant_key
        return self._attention_providers()

    def _base_dynamic_context_providers(self):
        return [
            DocumentContextProvider(
                document_repo=self._document_repo,
                specs=main_agent_document_specs(),
                source="main_agent",
            ),
            CurrentTimeContextProvider(source="main_agent"),
        ]

    def _attention_providers(self):
        providers = [
            ImageUploadAttentionProvider(),
            SelfieAgeAttentionProvider(
                image_repo=self._image_repo,
            ),
            *self._runtime_task_attention_providers(),
            *self._skincare_cabinet_runtime_task_providers(),
            EvidenceAttentionProvider(),
        ]
        deep_report_gate_provider = self._deep_report_gate_attention_provider()
        if deep_report_gate_provider is not None:
            providers.append(deep_report_gate_provider)
        return providers

    def _skincare_cabinet_runtime_task_providers(self):
        if self._runtime_task_repo is None or self._skincare_cabinet_repo is None:
            return []
        return [
            SkincareCabinetResearchRuntimeTaskAttentionProvider(
                runtime_task_repo=self._runtime_task_repo,
                cabinet_repo=self._skincare_cabinet_repo,
                emission_state=self._skincare_cabinet_task_attention_state,
            )
        ]

    def _runtime_task_attention_providers(self):
        if self._runtime_task_repo is None:
            return []
        providers = [
            ObligationRuntimeTaskAttentionProvider(
                runtime_task_repo=self._runtime_task_repo,
            )
        ]
        if self._action_usage_repo is not None:
            providers.append(
                SkinDiaryHandoffRuntimeTaskAttentionProvider(
                    runtime_task_repo=self._runtime_task_repo,
                    action_usage_repo=self._action_usage_repo,
                    emission_state=self._skin_diary_task_attention_state,
                )
            )
        providers.extend([
            SkinDiaryCompletionAttentionProvider(
                runtime_task_repo=self._runtime_task_repo,
                emission_state=self._skin_diary_completion_attention_state,
                completion_event_repo=self._completion_event_repo,
            ),
            ImageAnalysisCompletionAttentionProvider(
                runtime_task_repo=self._runtime_task_repo,
                emission_state=self._image_analysis_completion_attention_state,
                completion_event_repo=self._completion_event_repo,
            ),
            ImageAnalysisFailureAttentionProvider(
                runtime_task_repo=self._runtime_task_repo,
                emission_state=self._image_analysis_failure_attention_state,
                completion_event_repo=self._completion_event_repo,
            ),
            DeepReportOutcomeAttentionProvider(
                runtime_task_repo=self._runtime_task_repo,
                report_repo=self._deep_report_repo,
                emission_state=self._deep_report_outcome_attention_state,
                completion_event_repo=self._completion_event_repo,
            ),
        ])
        return providers

    def _deep_report_gate_attention_provider(self):
        if self._tool_invocation_store is None:
            return None
        if not hasattr(self._tool_invocation_store, "find_latest_for_tools"):
            return None

        from Mojing.harness.readiness import DeepReportReadiness, ImageAnalysisReadiness

        image_analysis_readiness = ImageAnalysisReadiness(
            image_repo=self._image_repo,
            document_repo=self._document_repo,
            runtime_task_repo=self._runtime_task_repo,
            skin_profile_repo=self._skin_profile_repo,
        )
        readiness = DeepReportReadiness(
            document_repo=self._document_repo,
            image_repo=self._image_repo,
            skin_profile_repo=self._skin_profile_repo,
            image_analysis_readiness=image_analysis_readiness,
            runtime_task_repo=self._runtime_task_repo,
            deep_report_repo=self._deep_report_repo,
        )
        return DeepReportGateAttentionProvider(
            tool_invocation_repo=self._tool_invocation_store,
            readiness=readiness,
            emission_state=self._deep_report_gate_attention_state,
        )

    def make_tool_registry(
        self,
        tenant_key: str,
        stage: str = "novice",
        capabilities: AgentCapabilities | None = None,
    ) -> ToolRegistry:
        """为指定租户 + 阶段组装 ToolRegistry。

        base_registry 的工具原样复制；tool_factories 按 tenant_key 实例化；
        device_tool_factories 只在本轮具备设备上下文时实例化；
        staged_tool_factories 只在当前 stage 命中时实例化。
        """
        capabilities = capabilities or AgentCapabilities()
        reg = ToolRegistry(
            runtime_services=self._base_registry.runtime_services,
            tool_lifecycle=self._make_tool_lifecycle(),
            invocation_store=self._tool_invocation_store,
        )
        for tool in self._base_registry.tools:
            if hasattr(tool, "set_context"):
                raise ValueError(
                    f"Contextual base tool '{tool.name}' must be registered via tool_factories"
                )
            reg.register(tool)
        for factory in self._tool_factories:
            reg.register(factory(tenant_key))
        if capabilities.device_enabled:
            for factory in self._device_tool_factories:
                reg.register(factory(tenant_key))
        for allowed_stages, factory in self._staged_tool_factories:
            if stage in allowed_stages:
                reg.register(factory(tenant_key))
        return reg

    def _make_tool_lifecycle(self):
        from simpleclaw.harness.lifecycle import ToolLifecycle
        from Mojing.harness.readiness import (
            DeepReportReadiness,
            HistoricalImageReadiness,
            ImageAnalysisReadiness,
            SkinDiaryGenerationReadiness,
        )
        from Mojing.harness.tool_gates import DeepReportGate, HistoricalImageGate, SkinDiaryGenerationGate

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
            deep_report_repo=self._deep_report_repo,
        )
        historical_image_readiness = HistoricalImageReadiness(image_repo=self._image_repo)
        skin_diary_generation_readiness = SkinDiaryGenerationReadiness(
            runtime_task_repo=self._runtime_task_repo,
        )
        return ToolLifecycle(before_tool_hooks=[
            HistoricalImageGate(historical_image_readiness),
            DeepReportGate(deep_report_readiness),
            SkinDiaryGenerationGate(skin_diary_generation_readiness),
        ])
