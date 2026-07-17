"""Generate structured skin diary results from the latest synced skin profile."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Awaitable, Callable, TYPE_CHECKING

import httpx
import json_repair
from loguru import logger

from simpleclaw.llm.chunks import TextChunk
from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.tools.base import Tool, ToolResult
from Mojing.agent.skin_profile_sync import extract_skin_summary
from Mojing.runtime.streams import MojingTaskStream
from Mojing.runtime.task_types import MojingTaskType
from Mojing.runtime.tool_results import tool_deduped, tool_submitted
from Mojing.storage.skin_diary_result_repo import SkinDiaryResultRepository
from Mojing.storage.skin_profile_repo import SkinProfileRepository
from Mojing.storage.skincare_cabinet_repo import SkincareCabinetRepository
from Mojing.services.weather import extract_city_from_user_profile
from Mojing.utils.skin_signals import signal_care_suggestions, signal_label, signal_regions, signal_severity
from Mojing.utils.skin_diary_time import (
    DIARY_SLOT_EVENING,
    DIARY_SLOT_MIDDAY,
    DIARY_SLOT_MORNING,
    strip_tz,
)

if TYPE_CHECKING:
    from simpleclaw.llm.base import LLMProvider
    from Mojing.storage.document_repo import DocumentRepository
    from Mojing.services.weather import BaiduWeatherService

_PROMPT_PATH = Path(__file__).parent.parent / "subagent" / "prompt" / "skin_diary_tool.md"
_KNOWN_REGIONS = ("额头", "鼻子", "左眼周", "右眼周", "左面颊", "右面颊", "下巴")
_REGION_ALIASES = {
    "前额": "额头",
    "额头": "额头",
    "鼻部": "鼻子",
    "鼻翼": "鼻子",
    "左眼": "左眼周",
    "右眼": "右眼周",
    "左眼周": "左眼周",
    "右眼周": "右眼周",
    "左脸颊": "左面颊",
    "右脸颊": "右面颊",
    "左面颊": "左面颊",
    "右面颊": "右面颊",
    "下颌": "下巴",
    "下巴": "下巴",
}
_STATE_ENUM = {"concern", "fluctuating", "stable", "improving", "excellent"}
# `skin_diary_tool.md` can describe generation-result states, while the
# stored diary row uses the current-state enum rendered by SkinDiarySubagent.
_PROMPT_STATE_TO_STORAGE_STATE = {
    "new_severe": "concern",
    "new_mild": "fluctuating",
    "unchanged": "stable",
}
_DEFAULT_SYSTEM_PROMPT = (
    "你是肌肤日记结构化生成器。只输出 JSON，不输出 markdown。"
    "内容要温和、具体、可执行，不像诊断书。"
)


class GenerateSkinDiaryTool(Tool):
    """Create a structured skin diary for the latest synced profile."""

    name = "generate_skin_diary"
    description = (
        "Generate and persist a structured skin diary from the latest synced skin profile. "
        "Use this only inside the skin_diary sub-agent when the user asks for a fresh diary analysis."
    )
    parameters = {
        "type": "object",
        "properties": {
            "confirmed_focus": {
                "type": "string",
                "description": "用户确认要纳入新版肌肤日记的关注点，如“下巴闭口、鼻翼黑头”。",
            },
            "declined_focus": {
                "type": "string",
                "description": "用户明确不想纳入新版肌肤日记的关注点。",
            },
            "source": {
                "type": "string",
                "enum": ["user_claim", "image_review", "mixed", "current_diary"],
                "description": "关注来源。用户主诉用 user_claim，图片复核用 image_review，二者都有用 mixed。",
            },
            "evidence": {
                "type": "string",
                "description": "用户原话或图片复核依据摘要，简短即可。",
            },
            "regeneration_reason": {
                "type": "string",
                "description": "本次生成/刷新原因，如用户确认当前关注、自动晨间生成、自动晚间生成。",
            },
            "notes": {
                "type": "string",
                "description": "额外护理或观察备注，简短即可。",
            },
        },
        "required": [],
    }

    needs_followup = True
    execution_mode = "durable"
    tool_category = "async_task"
    durable_action = "submitted"

    def __init__(
        self,
        *,
        llm: "LLMProvider",
        document_repo: "DocumentRepository",
        skin_profile_repo: SkinProfileRepository,
        result_repo: SkinDiaryResultRepository,
        cabinet_repo: SkincareCabinetRepository | None = None,
        weather_service: "BaiduWeatherService | None" = None,
        crop_endpoint_url: str = "",
        crop_timeout_s: int = 20,
    ) -> None:
        self._llm = llm
        self._document_repo = document_repo
        self._skin_profile_repo = skin_profile_repo
        self._result_repo = result_repo
        self._cabinet_repo = cabinet_repo
        self._weather_service = weather_service
        self._crop_endpoint_url = str(crop_endpoint_url or "").strip()
        self._crop_timeout_s = max(1, int(crop_timeout_s))
        self._tenant_key = "__default__"
        self._session_key = ""
        self._query = ""
        self._handoff_contract: dict[str, Any] = {}
        self._submitted_this_turn = False
        self._system_prompt = _load_generation_prompt()

    def set_context(
        self,
        *,
        tenant_key: str = "",
        session_key: str = "",
        query: str = "",
        handoff_contract: dict[str, Any] | None = None,
        **_,
    ) -> None:
        if tenant_key:
            self._tenant_key = tenant_key
        if session_key:
            self._session_key = session_key
        self._query = str(query or "")
        self._handoff_contract = dict(handoff_contract or {})
        self._submitted_this_turn = False

    async def prepare_task(self, **params: Any) -> TaskEnvelope | ToolResult:
        if self._submitted_this_turn:
            return _same_turn_deduped_result()

        tenant_key = self._tenant_key.strip()
        if not tenant_key:
            return _json_result(_build_result(
                action="invalid",
                ok=False,
                status="missing_tenant",
                error="Skin diary generation is missing tenant context.",
            ))

        profile = await self._skin_profile_repo.get_latest(tenant_key)
        if profile is None:
            return _json_result(_build_result(
                action="error",
                ok=False,
                status="missing_skin_profile",
                error="No skin profile is available for skin diary generation.",
            ))

        generation_input = _normalize_generation_input(params)
        payload = {
            "tenant_key": tenant_key,
            "session_key": self._session_key.strip(),
            "query": self._query,
            "generation_input": generation_input,
        }
        payload.update(_handoff_lineage_payload(self._handoff_contract))
        return TaskEnvelope(
            task_type=MojingTaskType.SKIN_DIARY_GENERATION,
            payload=payload,
            stream=MojingTaskStream.SKIN_DIARY,
            tenant_key=tenant_key,
            session_key=self._session_key.strip(),
            scope_key=f"{MojingTaskType.SKIN_DIARY_GENERATION}:{tenant_key}",
            service_role="mojing:skin-diary-generation",
        )

    async def on_task_submitted(self, task: TaskEnvelope, queue_id: str) -> None:
        self._submitted_this_turn = True
        logger.info(
            "generate_skin_diary queued: tenant={} session={} task_id={} queue_id={}",
            self._tenant_key, self._session_key, task.task_id, queue_id,
        )

    def durable_result(self, task: TaskEnvelope, queue_id: str) -> ToolResult:
        return tool_submitted(
            tool=self.name,
            task_id=task.task_id,
            queue_id=queue_id,
            created_new_job=True,
            estimated_seconds=30,
            message_focus=(
                "新版肌肤日记已开始生成，通常需要十几到三十秒。"
                "请告诉用户正在生成，可以先继续聊当前关注；不要说已经生成完成。"
            ),
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        if self._submitted_this_turn:
            return _same_turn_deduped_result()
        result = await self.generate(generation_input=_normalize_generation_input(kwargs))
        return _json_result(result)

    async def generate(
        self,
        *,
        tenant_key: str = "",
        session_key: str = "",
        query: str = "",
        generation_input: dict[str, Any] | None = None,
        progress_callback: Callable[[str, int, str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        async def emit_progress(stage_code: str, progress_percent: int, current_title: str) -> None:
            if progress_callback is None:
                return
            try:
                await progress_callback(stage_code, progress_percent, current_title)
            except Exception as exc:
                logger.warning(
                    "generate_skin_diary progress callback failed tenant={} stage={} error={}",
                    tenant_key or self._tenant_key,
                    stage_code,
                    exc,
                )

        if tenant_key:
            self._tenant_key = tenant_key
        if session_key:
            self._session_key = session_key
        if query:
            self._query = query

        tenant_key = self._tenant_key.strip()
        if not tenant_key:
            return _build_result(
                action="invalid",
                ok=False,
                status="missing_tenant",
                error="Skin diary generation is missing tenant context.",
            )
        profile = await self._skin_profile_repo.get_latest(tenant_key)
        if profile is None:
            return _build_result(
                action="error",
                ok=False,
                status="missing_skin_profile",
                error="No skin profile is available for skin diary generation.",
            )

        await emit_progress("diary_analysis", 25, "当日状态分析")
        user_profile = await self._read_user_profile(tenant_key)
        weather_reference = await self._read_weather_reference(user_profile)
        cabinet_products = await self._read_cabinet_products(tenant_key)
        signals = self._skin_profile_repo.parse_json_field(profile.get("signals_json")) or []
        if not isinstance(signals, list):
            signals = []

        generation_context = _parse_generation_context(self._query)
        generation_context.update(_normalize_generation_input(generation_input or {}))
        issue_targets = self._collect_issue_targets(signals)
        issue_targets = await self._attach_crop_urls(
            issue_targets,
            image_url=str(profile.get("image_url") or "").strip(),
        )
        previous = await self._result_repo.get_latest(tenant_key)

        await emit_progress("focus_summary", 45, "关注点整理")
        await emit_progress("routine_generation", 70, "护肤路径生成")
        model_payload = await self._ask_model(
            tenant_key=tenant_key,
            user_profile=user_profile,
            cabinet_products=cabinet_products,
            weather_reference=weather_reference,
            profile=profile,
            issue_targets=issue_targets,
            previous_result=previous,
            generation_context=generation_context,
        )
        payload = self._merge_model_payload(
            model_payload=model_payload,
            profile=profile,
            issue_targets=issue_targets,
            previous_result=previous,
            generation_context=generation_context,
            weather_reference=weather_reference,
        )

        await emit_progress("content_finalize", 90, "日记内容整理")
        analyzed_at = _coerce_datetime(profile.get("created_at")) or datetime.now()
        create_time = _resolve_create_time(generation_context) or datetime.now()
        result_id = await self._result_repo.create_result(
            tenant_key=tenant_key,
            analyzed_at=analyzed_at,
            create_time=create_time,
            state=payload["state"],
            summary=payload["summary"],
            chips=payload["chips"],
            morning_steps=payload["morning_steps"],
            evening_steps=payload["evening_steps"],
            raw_output=payload["raw_output"],
            creator="generate_skin_diary",
        )
        self._submitted_this_turn = True
        return _build_result(
            action="executed",
            ok=True,
            status="generated",
            result_id=result_id,
            created_new_job=True,
            summary=payload["summary"],
            message_focus=(
                "肌肤日记已生成并保存。请基于系统随后可见的结构化结果，"
                "自然告诉用户已生成；如果当前轮还没注入新结果，只简短说明已生成完成。"
            ),
            state=payload["state"],
            card=_build_skin_diary_card(
                result_id=result_id,
                payload=payload,
                analyzed_at=analyzed_at,
                create_time=create_time,
            ),
        )

    async def _read_user_profile(self, tenant_key: str) -> str:
        try:
            return (await self._document_repo.get(tenant_key, "USER.md") or "").strip()
        except Exception as exc:
            logger.warning("generate_skin_diary failed to read USER.md for {}: {}", tenant_key, exc)
            return ""

    async def _read_cabinet_products(self, tenant_key: str) -> list[dict[str, Any]]:
        if self._cabinet_repo is None:
            return []
        try:
            return await self._cabinet_repo.list_in_cabinet(user_id=tenant_key, limit=5)
        except Exception as exc:
            logger.warning("generate_skin_diary failed to read cabinet products for {}: {}", tenant_key, exc)
            return []

    async def _read_weather_reference(self, user_profile: str) -> dict[str, Any] | None:
        if self._weather_service is None:
            return None
        city = extract_city_from_user_profile(user_profile)
        if not city:
            return None
        try:
            payload = await self._weather_service.query(
                location=city,
                focus="skincare",
                time_scope="today",
            )
        except Exception as exc:
            logger.warning("generate_skin_diary weather lookup failed city={} err={}", city, exc)
            return None
        if not payload.get("ok"):
            logger.info(
                "generate_skin_diary weather lookup skipped city={} status={}",
                city,
                payload.get("status"),
            )
            return None
        return {
            "location_query": payload.get("location_query") or city,
            "time_scope": payload.get("time_scope") or "today",
            "summary": payload.get("user_visible_summary") or "",
        }

    async def _ask_model(
        self,
        *,
        tenant_key: str,
        user_profile: str,
        cabinet_products: list[dict[str, Any]],
        weather_reference: dict[str, Any] | None,
        profile: dict[str, Any],
        issue_targets: list[dict[str, Any]],
        previous_result: dict[str, Any] | None,
        generation_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        summary = extract_skin_summary(profile, parse_json=self._skin_profile_repo.parse_json_field)
        prompt_text = _build_user_prompt(
            tenant_key=tenant_key,
            user_profile=user_profile,
            cabinet_products=cabinet_products,
            weather_reference=weather_reference,
            summary=summary,
            issue_targets=issue_targets,
            previous_result=previous_result,
            generation_context=generation_context,
        )
        content: list[Any] = [{"type": "text", "text": prompt_text}]
        for item in issue_targets:
            if not item.get("image_urls"):
                continue
            content.append({
                "type": "text",
                "text": f"标签：{item['label']}；部位：{'、'.join(item['regions']) or '未标注'}。",
            })
            for url in item["image_urls"]:
                content.append({"type": "image_url", "image_url": {"url": url}})

        try:
            text = await _complete_text(
                self._llm,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": content},
                ],
                max_tokens=1800,
                temperature=0.2,
            )
        except Exception as exc:
            logger.warning("generate_skin_diary model call failed for {}: {}", tenant_key, exc)
            return None
        return _parse_json(text)

    def _merge_model_payload(
        self,
        *,
        model_payload: dict[str, Any] | None,
        profile: dict[str, Any],
        issue_targets: list[dict[str, Any]],
        previous_result: dict[str, Any] | None,
        generation_context: dict[str, Any],
        weather_reference: dict[str, Any] | None,
    ) -> dict[str, Any]:
        previous_labels = {
            str(item.get("label") or "").strip()
            for item in ((previous_result or {}).get("chips") or [])
            if isinstance(item, dict)
        }
        payload = model_payload or {}
        raw_chips = payload.get("chips")
        targets_by_label = {
            str(target.get("label") or "").strip(): target
            for target in issue_targets
            if str(target.get("label") or "").strip()
        }

        chips: list[dict[str, Any]] = []
        if isinstance(raw_chips, list):
            for item in raw_chips[:5]:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label") or "").strip()
                if label:
                    target = targets_by_label.get(label, {})
                    fallback_analysis = f"该关注主要围绕{label}持续观察。"
                    fallback_suggestion = (
                        "、".join((target.get("care_suggestions") or [])[:2])
                        or "建议温和护理并持续观察变化。"
                    )
                    chips.append({
                        "label": label,
                        "isNew": label not in previous_labels,
                        "severity": _normalize_chip_severity(
                            item.get("severity"),
                            fallback=target.get("severity"),
                        ),
                        "image_urls": list(target.get("image_urls") or []),
                        "analysis": str(item.get("analysis") or fallback_analysis).strip(),
                        "suggestion": str(item.get("suggestion") or fallback_suggestion).strip(),
                    })

        if not chips:
            for target in issue_targets[:5]:
                label = target["label"]
                fallback_analysis = f"该问题主要集中在{'、'.join(target['regions']) or '局部区域'}。"
                fallback_suggestion = "、".join(target["care_suggestions"][:2]) or "建议温和护理并持续观察变化。"
                chips.append({
                    "label": label,
                    "isNew": label not in previous_labels,
                    "severity": _normalize_chip_severity(target.get("severity")),
                    "image_urls": list(target["image_urls"]),
                    "analysis": fallback_analysis,
                    "suggestion": fallback_suggestion,
                })

        summary_map = extract_skin_summary(profile, parse_json=self._skin_profile_repo.parse_json_field)
        state = _normalize_state(
            payload.get("state") or payload.get("status"),
            issue_targets=issue_targets,
            previous_result=previous_result,
        )
        summary = str(payload.get("summary") or "").strip()
        if not summary:
            summary = str(summary_map.get("skin_overall_state") or "").strip()
        if not summary:
            summary = f"本次检测识别出 {len(issue_targets)} 个重点关注点，建议按节律持续护理。"

        morning_steps = _normalize_steps(
            payload.get("morning_steps"),
            fallback_focus=[item["label"] for item in issue_targets],
            fallback_title_prefix="早间护理",
        )
        evening_steps = _normalize_steps(
            payload.get("evening_steps"),
            fallback_focus=[item["label"] for item in issue_targets],
            fallback_title_prefix="晚间护理",
        )
        return {
            "state": state,
            "summary": summary,
            "chips": chips,
            "morning_steps": morning_steps,
            "evening_steps": evening_steps,
            "raw_output": {
                "model_payload": payload,
                "issue_targets": issue_targets,
                "generation_context": generation_context,
                "weather_reference": weather_reference,
            },
        }

    async def _attach_crop_urls(
        self,
        issue_targets: list[dict[str, Any]],
        *,
        image_url: str,
    ) -> list[dict[str, Any]]:
        if not self._crop_endpoint_url or not image_url:
            for item in issue_targets:
                item["image_urls"] = []
            return issue_targets
        semaphore = asyncio.Semaphore(4)

        async def _run(region: str) -> str | None:
            async with semaphore:
                return await self._crop_region(image_url=image_url, region=region)

        for item in issue_targets:
            results = await asyncio.gather(
                *[_run(region) for region in item["regions"]],
                return_exceptions=True,
            )
            image_urls: list[str] = []
            for result in results:
                if isinstance(result, Exception):
                    logger.warning("generate_skin_diary crop task failed: {}", result)
                    continue
                if isinstance(result, str) and result.strip():
                    image_urls.append(result.strip())
            item["image_urls"] = image_urls
        return issue_targets

    async def _crop_region(self, *, image_url: str, region: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=float(self._crop_timeout_s)) as client:
                response = await client.post(
                    self._crop_endpoint_url,
                    json={"image_url": image_url, "region": region},
                )
            if response.status_code < 200 or response.status_code >= 300:
                logger.warning(
                    "generate_skin_diary crop failed region={} status={} body={}",
                    region,
                    response.status_code,
                    response.text[:200],
                )
                return None
            payload = response.json()
        except Exception as exc:
            logger.warning("generate_skin_diary crop request failed region={} error={}", region, exc)
            return None
        url = str(payload.get("url") or "").strip()
        return url or None

    @classmethod
    def _collect_issue_targets(cls, signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for signal in signals:
            if not isinstance(signal, dict):
                continue
            label = signal_label(signal)
            if not label:
                continue
            bucket = grouped.setdefault(label, {
                "label": label,
                "regions": [],
                "care_suggestions": [],
                "image_urls": [],
                "severity": "",
            })
            severity = _normalize_chip_severity(signal_severity(signal))
            if _severity_rank(severity) > _severity_rank(str(bucket.get("severity") or "")):
                bucket["severity"] = severity
            for region in cls._normalize_regions(signal_regions(signal)):
                if region not in bucket["regions"]:
                    bucket["regions"].append(region)
            for tip in signal_care_suggestions(signal):
                if tip and tip not in bucket["care_suggestions"]:
                    bucket["care_suggestions"].append(tip)
        return list(grouped.values())

    @classmethod
    def _normalize_regions(cls, values: list[str]) -> list[str]:
        regions: list[str] = []
        for value in values:
            for part in re.split(r"[·、,，/；;\s]+", str(value or "")):
                token = part.strip()
                if not token:
                    continue
                normalized = _REGION_ALIASES.get(token, token)
                if normalized in _KNOWN_REGIONS and normalized not in regions:
                    regions.append(normalized)
        return regions


async def _complete_text(
    llm: "LLMProvider",
    *,
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float,
) -> str:
    parts: list[str] = []
    async for chunk in llm.stream_with_retry(
        messages,
        tools=None,
        max_tokens=max_tokens,
        temperature=temperature,
    ):
        if isinstance(chunk, TextChunk):
            parts.append(chunk.token)
    return "".join(parts).strip()


def _load_generation_prompt() -> str:
    try:
        text = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return _DEFAULT_SYSTEM_PROMPT
    return text or _DEFAULT_SYSTEM_PROMPT


def _build_user_prompt(
    *,
    tenant_key: str,
    user_profile: str,
    cabinet_products: list[dict[str, Any]],
    weather_reference: dict[str, Any] | None,
    summary: dict[str, Any],
    issue_targets: list[dict[str, Any]],
    previous_result: dict[str, Any] | None,
    generation_context: dict[str, Any],
) -> str:
    issue_lines = [
        (
            f"- {item['label']}｜严重程度：{item.get('severity') or '未标注'}"
            f"｜部位：{'、'.join(item['regions']) or '未标注'}"
            f"｜护理建议：{'、'.join(item['care_suggestions']) or '暂无'}"
        )
        for item in issue_targets
    ]
    previous_block = json.dumps(previous_result, ensure_ascii=False, indent=2) if previous_result else "（无历史肌肤日记）"
    context_block = json.dumps(generation_context, ensure_ascii=False, indent=2) if generation_context else "（手动或未指定）"
    cabinet_lines: list[str] = []
    for item in cabinet_products[:5]:
        ingredients = "、".join(list(item.get("core_ingredients") or [])[:4]) or "暂无"
        efficacy = "、".join(list(item.get("core_efficacy") or [])[:4]) or "暂无"
        category = str(item.get("category") or "护肤品").strip() or "护肤品"
        usage_status = str(item.get("usage_status") or "using").strip() or "using"
        cabinet_lines.append(
            f"- {item.get('brand') or ''} {item.get('product_name') or ''}｜类型：{category}｜使用状态：{usage_status}｜核心成分：{ingredients}｜核心功效：{efficacy}"
        )
    cabinet_block = "\n".join(cabinet_lines) if cabinet_lines else "（暂无已入柜护肤品）"
    weather_block = _format_weather_reference(weather_reference)
    return (
        "请基于以下动态输入生成一份肌肤日记 JSON。输出格式、字段含义、用户关注规则和文案边界"
        "全部以系统提示词为准。\n\n"
        f"## Tenant\n{tenant_key}\n\n"
        f"## 生成上下文\n{context_block}\n\n"
        f"## 用户画像 USER.md\n{(user_profile or '（暂无）')[:3000]}\n\n"
        f"## 已入柜护肤品\n{cabinet_block}\n\n"
        f"## 今日天气参考\n{weather_block}\n\n"
        "## 最新皮肤画像摘要\n"
        f"- 肤龄阶段：{summary.get('skin_stage') or '未知'}\n"
        f"- 肤色调：{summary.get('skin_tone_type') or '未知'}\n"
        f"- 肤质：{summary.get('skin_type') or '未知'}\n"
        f"- 主要肤况：{summary.get('skin_concern') or '暂无'}\n"
        f"- 问题分布：{summary.get('skin_concern_distribution') or '暂无'}\n"
        f"- 皮肤总评：{summary.get('skin_overall_state') or '暂无'}\n"
        f"- 皮肤优势：{summary.get('skin_advantages') or '暂无'}\n"
        f"- 护理关注点：{summary.get('skin_care_focus') or '暂无'}\n\n"
        f"## 候选标签\n{chr(10).join(issue_lines) if issue_lines else '- 暂无显著局部问题'}\n\n"
        f"## 上一版肌肤日记\n{previous_block}"
    )


def _format_weather_reference(weather_reference: dict[str, Any] | None) -> str:
    if not weather_reference:
        return "（USER.md 未提供明确城市，未查询天气）"
    location = str(weather_reference.get("location_query") or "").strip()
    summary = str(weather_reference.get("summary") or "").strip()
    if not summary:
        return "（天气查询未返回有效摘要）"
    prefix = f"地点：{location}\n" if location else ""
    return prefix + summary


def _parse_json(content: str | None) -> dict[str, Any] | None:
    if not content:
        return None
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    try:
        repaired = json_repair.loads(text)
        return repaired if isinstance(repaired, dict) else None
    except Exception:
        logger.warning("generate_skin_diary failed to parse JSON: {}", text[:200])
        return None


def _normalize_chip_severity(raw: Any, *, fallback: Any = None) -> str:
    for value in (raw, fallback):
        text = str(value or "").strip()
        if not text:
            continue
        if text == "重度" or text == "中度":
            return "重度"
        if text == "轻度":
            return "轻度"
        try:
            if int(text) >= 2:
                return "重度"
            if int(text) == 1:
                return "轻度"
        except (TypeError, ValueError):
            pass
    return "轻度"


def _severity_rank(severity: str) -> int:
    if severity == "重度":
        return 2
    if severity == "轻度":
        return 1
    return 0


def _normalize_steps(
    raw_steps: Any,
    *,
    fallback_focus: list[str],
    fallback_title_prefix: str,
) -> list[dict[str, Any]]:
    if not isinstance(raw_steps, list):
        raw_steps = []
    steps: list[dict[str, Any]] = []
    for index, item in enumerate(raw_steps[:4], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip() or f"{fallback_title_prefix}{index}"
        usage = str(item.get("usage") or "").strip() or "按常规节奏温和执行。"
        effect = str(item.get("effect") or "").strip() or "帮助维持肤况稳定。"
        focus_area = str(item.get("focus_area") or "").strip()
        if not focus_area and fallback_focus:
            focus_area = fallback_focus[min(index - 1, len(fallback_focus) - 1)]
        steps.append({
            "order": index,
            "title": title,
            "usage": usage,
            "effect": effect,
            "focus_area": focus_area,
        })
    if steps:
        return steps
    return [
        {
            "order": index,
            "title": f"{fallback_title_prefix}{index}",
            "usage": "优先处理当前重点区域，避免叠加过多刺激。",
            "effect": "帮助问题区域逐步稳定。",
            "focus_area": focus,
        }
        for index, focus in enumerate(fallback_focus[:3], start=1)
    ]


def _fallback_state(issue_targets: list[dict[str, Any]], previous_result: dict[str, Any] | None) -> str:
    count = len(issue_targets)
    if count >= 4:
        return "concern"
    if count >= 2:
        return "fluctuating"
    if count == 1:
        return "stable" if previous_result else "concern"
    return "excellent"


def _normalize_state(
    raw_state: Any,
    *,
    issue_targets: list[dict[str, Any]],
    previous_result: dict[str, Any] | None,
) -> str:
    state = str(raw_state or "").strip().lower()
    state = _PROMPT_STATE_TO_STORAGE_STATE.get(state, state)
    if state in _STATE_ENUM:
        return state
    return _fallback_state(issue_targets, previous_result)


def _build_skin_diary_card(
    *,
    result_id: int,
    payload: dict[str, Any],
    analyzed_at: datetime,
    create_time: datetime,
) -> dict[str, Any]:
    return {
        "type": "skin_diary_card",
        "result_id": result_id,
        "state": payload.get("state"),
        "summary": payload.get("summary"),
        "chips": payload.get("chips") or [],
        "morning_steps": payload.get("morning_steps") or [],
        "evening_steps": payload.get("evening_steps") or [],
        "analyzed_at": analyzed_at.strftime("%Y-%m-%d %H:%M:%S"),
        "create_time": create_time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _normalize_generation_input(raw: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "confirmed_focus",
        "declined_focus",
        "source",
        "evidence",
        "regeneration_reason",
        "notes",
    }
    result: dict[str, Any] = {}
    for key in allowed:
        value = raw.get(key)
        if value is None:
            continue
        text = " ".join(str(value).split()).strip()
        if text:
            result[key] = text[:600]
    source = str(result.get("source") or "").strip()
    if source and source not in {"user_claim", "image_review", "mixed", "current_diary"}:
        result.pop("source", None)
    return result


def _handoff_lineage_payload(contract: dict[str, Any]) -> dict[str, str]:
    if not isinstance(contract, dict):
        return {}
    out: dict[str, str] = {}
    for key in (
        "parent_handoff_task_id",
        "original_user_query",
        "origin_session_key",
        "source_task_id",
        "source_image_id",
        "source_image_ref",
    ):
        value = str(contract.get(key) or "").strip()
        if value:
            out[key] = value
    return out


def _build_result(
    *,
    action: str,
    ok: bool,
    status: str | None = None,
    result_id: int | None = None,
    created_new_job: bool = False,
    error: str | None = None,
    summary: str | None = None,
    message_focus: str | None = None,
    task_id: str | None = None,
    queue_id: str | None = None,
    estimated_seconds: int | None = None,
    state: str | None = None,
    card: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "action": action,
        "status": status,
        "result_id": result_id,
        "created_new_job": created_new_job,
        "error": error,
        "summary": summary,
        "message_focus": message_focus,
        "task_id": task_id,
        "queue_id": queue_id,
        "estimated_seconds": estimated_seconds,
        "state": state,
        "card": card,
    }


def _same_turn_deduped_result() -> ToolResult:
    return tool_deduped(
        reason="already_submitted_in_turn",
        phase="already_submitted",
        source="turn_memory",
        message_focus=(
            "本轮已经提交过肌肤日记生成任务，请沿用第一次工具结果回复用户。"
            "不要重复触发，也不要说新版肌肤日记已经生成完成。"
        ),
    )


def _json_result(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(content=json.dumps(payload, ensure_ascii=False), ok=bool(payload.get("ok")))


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_generation_context(message: str) -> dict[str, str]:
    text = str(message or "")
    result: dict[str, str] = {}
    date_match = re.search(r"业务日期\s*=\s*(\d{4}-\d{2}-\d{2})", text)
    slot_match = re.search(r"日记时段\s*=\s*([a-zA-Z_]+|晨间|早间|午间|晚间)", text)
    reason_match = re.search(r"生成原因\s*=\s*([a-zA-Z0-9_:-]+)", text)
    triggered_match = re.search(r"北京时间触发时间\s*=\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", text)
    if date_match:
        result["diary_date"] = date_match.group(1)
    if slot_match:
        result["diary_slot"] = _normalize_slot(slot_match.group(1))
    if reason_match:
        result["generation_reason"] = reason_match.group(1)
    if triggered_match:
        result["triggered_at_beijing"] = triggered_match.group(1)
    return result


def _normalize_slot(value: str) -> str:
    raw = str(value or "").strip()
    mapping = {
        "晨间": DIARY_SLOT_MORNING,
        "早间": DIARY_SLOT_MORNING,
        "午间": DIARY_SLOT_MIDDAY,
        "晚间": DIARY_SLOT_EVENING,
    }
    return mapping.get(raw, raw.lower())


def _resolve_create_time(generation_context: dict[str, str]) -> datetime | None:
    triggered = _coerce_datetime(generation_context.get("triggered_at_beijing"))
    if triggered is not None:
        return strip_tz(triggered) if triggered.tzinfo is not None else triggered
    raw_date = generation_context.get("diary_date")
    slot = generation_context.get("diary_slot")
    if not raw_date or not slot:
        return None
    try:
        business_date = date.fromisoformat(raw_date)
    except ValueError:
        return None
    representative = {
        DIARY_SLOT_MORNING: time(hour=8),
        DIARY_SLOT_MIDDAY: time(hour=12),
        DIARY_SLOT_EVENING: time(hour=20),
    }.get(slot)
    if representative is None:
        return None
    return datetime.combine(business_date, representative)
