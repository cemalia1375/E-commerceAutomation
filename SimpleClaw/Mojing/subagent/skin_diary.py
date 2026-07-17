"""SkinDiarySubagent — 肌肤日记子 Agent 的业务装配层。

它和主 Agent 使用同一套收口协议：
  - Stable prompt: skin_diary.md + workspace/SOUL.md + compliance.md
  - Dynamic context: USER/SOUL/SKIN_DIARY_TODO + 最新日记事实
  - Attention: 图片上传、证据召回提示、handoff 执行要求
  - Tools: generate_skin_diary / retrieve_evidence
  - Post-turn: skin_diary_postprocess / cold_path
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from simpleclaw.context.builder import ContextBuilder
from simpleclaw.harness.hooks import PostrunHook
from simpleclaw.subagent.base import SubagentBase
from simpleclaw.tools.registry import ToolRegistry

from Mojing.context.skin_diary import (
    SkinDiaryHandoffContractAttentionProvider,
    SkinDiaryImageUploadAttentionProvider,
    SkinDiaryResultContextProvider,
    skin_diary_document_provider,
)
from Mojing.skills import get_skin_diary_skill_registry
from Mojing.storage.database import Database
from Mojing.storage.document_repo import DocumentRepository
from Mojing.storage.memory_repo import MySQLMemory
from Mojing.storage.obligation_repo import ObligationRepository
from Mojing.storage.session_repo import SessionRepository
from Mojing.storage.skin_diary_result_repo import SkinDiaryResultRepository
from Mojing.storage.skin_profile_repo import SkinProfileRepository

if TYPE_CHECKING:
    from simpleclaw.llm.base import LLMProvider
    from simpleclaw.runtime.side_effects import PostTurnEffects
    from Mojing.storage.image_repo import ImageRepository
    from Mojing.storage.runtime_task_repo import RuntimeTaskRepository
    from Mojing.storage.skincare_cabinet_repo import SkincareCabinetRepository
    from Mojing.services.weather import BaiduWeatherService


_PROMPT_PATH = Path(__file__).parent / "prompt" / "skin_diary.md"


def _load_prompt() -> str:
    """加载子 Agent 的 stable prompt；文件缺失直接抛 FileNotFoundError。"""
    return _PROMPT_PATH.read_text(encoding="utf-8").strip()


class SkinDiarySubagent(SubagentBase):
    """肌肤日记子 Agent 的具体实现。"""

    name = "skin_diary"

    def __init__(
        self,
        db: Database,
        document_repo: DocumentRepository,
        llm: "LLMProvider",
        hook_llm: "LLMProvider",
        obligation_repo: ObligationRepository,
        session_repo: SessionRepository,
        llm_cache_repo: object | None = None,
        image_repo: "ImageRepository | None" = None,
        crop_endpoint_url: str = "",
        crop_timeout_s: int = 20,
        runtime_task_repo: "RuntimeTaskRepository | None" = None,
        skincare_cabinet_repo: "SkincareCabinetRepository | None" = None,
        weather_service: "BaiduWeatherService | None" = None,
        post_turn_effects: "PostTurnEffects | None" = None,
    ) -> None:
        self._db = db
        self._document_repo = document_repo
        self._llm = llm
        self._result_repo = SkinDiaryResultRepository(db)
        self._skin_profile_repo = SkinProfileRepository(db)
        self._image_repo = image_repo
        self._skincare_cabinet_repo = skincare_cabinet_repo
        self._crop_endpoint_url = str(crop_endpoint_url or "").strip()
        self._crop_timeout_s = max(1, int(crop_timeout_s))
        self._runtime_task_repo = runtime_task_repo
        self._weather_service = weather_service
        self._prompt = _load_prompt()
        self._skill_registry = get_skin_diary_skill_registry()

        from Mojing.config import load_compliance, load_workspace_section
        self._workspace_soul = load_workspace_section("SOUL.md")
        self._compliance = load_compliance()

        from Mojing.agent.cold_path import ColdPathHook
        from Mojing.subagent.skin_diary_postprocess import SkinDiaryPostprocessHook

        self._postprocess_hook: PostrunHook = SkinDiaryPostprocessHook(
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
        return f"skin_diary:{tenant_key}"

    def matches(self, session_key: str) -> bool:
        return session_key.startswith("skin_diary:")

    async def make_context_builder(self, tenant_key: str) -> ContextBuilder:
        stable = [self._prompt]
        if self._workspace_soul:
            stable.append(self._workspace_soul)
        if self._compliance:
            stable.append(self._compliance)
        return ContextBuilder(
            stable_sections=stable,
            stable_prompt_providers=self.make_stable_prompt_providers(tenant_key),
            dynamic_context_providers=self.make_dynamic_context_providers(tenant_key),
            attention_providers=self.make_attention_providers(tenant_key),
            skill_registry=self._skill_registry,
            include_skill_index=True,
            tenant_key=tenant_key,
            cache_lane="skin_diary",
            cache_session_key=self.session_key_for(tenant_key),
        )

    def make_dynamic_context_providers(self, tenant_key: str):
        del tenant_key
        return [
            skin_diary_document_provider(self._document_repo),
            SkinDiaryResultContextProvider(
                self._result_repo,
                runtime_task_repo=getattr(self, "_runtime_task_repo", None),
            ),
        ]

    def make_attention_providers(self, tenant_key: str):
        del tenant_key
        return [
            SkinDiaryHandoffContractAttentionProvider(),
            SkinDiaryImageUploadAttentionProvider(),
        ]

    def make_tool_registry(self, tenant_key: str) -> ToolRegistry:
        from Mojing.tools.generate_skin_diary import GenerateSkinDiaryTool
        from Mojing.tools.retrieve_evidence import RetrieveEvidenceTool
        from simpleclaw.harness.lifecycle import ToolLifecycle
        from Mojing.harness.readiness import HistoricalImageReadiness, SkinDiaryGenerationReadiness
        from Mojing.harness.tool_gates import HistoricalImageGate, SkinDiaryGenerationGate
        from simpleclaw.tools.builtin.skill import LoadSkillTool, UnloadSkillTool

        before_tool_hooks = [
            SkinDiaryGenerationGate(SkinDiaryGenerationReadiness(
                document_repo=self._document_repo,
                skin_profile_repo=self._skin_profile_repo,
                skin_diary_result_repo=self._result_repo,
                runtime_task_repo=self._runtime_task_repo,
            )),
        ]
        if self._image_repo is not None:
            before_tool_hooks.append(
                HistoricalImageGate(HistoricalImageReadiness(image_repo=self._image_repo)),
            )

        registry = ToolRegistry(tool_lifecycle=ToolLifecycle(before_tool_hooks=before_tool_hooks))
        registry.register(LoadSkillTool())
        registry.register(UnloadSkillTool())
        registry.register(GenerateSkinDiaryTool(
            llm=self._llm,
            document_repo=self._document_repo,
            skin_profile_repo=self._skin_profile_repo,
            result_repo=self._result_repo,
            cabinet_repo=self._skincare_cabinet_repo,
            weather_service=self._weather_service,
            crop_endpoint_url=self._crop_endpoint_url,
            crop_timeout_s=self._crop_timeout_s,
        ))
        if self._image_repo is not None:
            registry.register(RetrieveEvidenceTool(
                llm=self._llm,
                memory=MySQLMemory(db=self._db, tenant_key=tenant_key, source="skin_diary"),
                image_repo=self._image_repo,
            ))
        return registry

    def make_postprocess_hook(self) -> PostrunHook:
        return self._postprocess_hook

    def make_cold_path_hook(self) -> PostrunHook:
        return self._cold_path_hook

    def make_post_turn_effects(self) -> "PostTurnEffects | None":
        return self._post_turn_effects
