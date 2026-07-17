"""Deep report context and attention providers.

This module mirrors the skin diary sub-agent shape: the sub-agent only wires
provider groups, while this module owns deep-report-specific DB reads and
wording.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from simpleclaw.context.providers import (
    AttentionPacket,
    ContextBuildContext,
    ContextSection,
)

from Mojing.context.providers import DocumentContextProvider, DocumentContextSpec
from Mojing.harness.readiness.base import normalize_status
from Mojing.runtime.task_types import MojingTaskType

if TYPE_CHECKING:
    from Mojing.storage.deep_report_repo import DeepReportRepository
    from Mojing.storage.runtime_task_repo import RuntimeTaskRepository


_MAX_TEXT = 900
_MAX_LIST_ITEMS = 8
_MAX_DEPTH = 4
_REPORT_BLOCKING_STATUSES = {"queued", "running", "wait_external", "failed"}

def deep_report_document_provider(document_repo) -> DocumentContextProvider:
    """Dynamic tenant documents used by the deep report sub-agent."""

    return DocumentContextProvider(
        document_repo=document_repo,
        specs=[
            DocumentContextSpec("USER.md", _plain_document),
            DocumentContextSpec("SOUL.md", _format_soul_document),
        ],
        source="deep_report_documents",
    )


@dataclass(slots=True)
class DeepReportContextProvider:
    """Inject the selected/latest deep report, without stale-report leakage."""

    report_repo: "DeepReportRepository"
    runtime_task_repo: "RuntimeTaskRepository | None" = None
    source: str = "deep_report"

    async def collect_dynamic_context(
        self,
        ctx: ContextBuildContext,
    ) -> list[ContextSection]:
        report_id = str(ctx.metadata.get("report_id") or "").strip()

        if not report_id:
            task = await self._latest_task(ctx.tenant_key)
            status = _task_status(task)
            if status in _REPORT_BLOCKING_STATUSES:
                return []

        report = await self._load_report(ctx.tenant_key, report_id=report_id)
        if report is None:
            return []

        sections = [
            _format_deep_report_session_state(report),
            _format_deep_report_freshness(report),
            _format_deep_report(report),
        ]
        return [
            ContextSection(content=section, source=self.source)
            for section in sections
            if section and section.strip()
        ]

    async def _latest_task(self, tenant_key: str) -> dict[str, Any] | None:
        if self.runtime_task_repo is None:
            return None
        try:
            return await self.runtime_task_repo.find_latest_task_for(
                tenant_key=tenant_key,
                task_type=MojingTaskType.DEEP_RESEARCH,
            )
        except Exception as exc:
            logger.warning("deep_report context fetch runtime task failed: tenant={} err={}", tenant_key, exc)
            return None

    async def _load_report(
        self,
        tenant_key: str,
        *,
        report_id: str,
    ) -> dict[str, Any] | None:
        if report_id:
            try:
                return await self.report_repo.find_by_report_id_full(tenant_key, report_id)
            except Exception as exc:
                logger.warning(
                    "deep_report find_by_report_id_full failed: tenant={} report_id={} err={}",
                    tenant_key, report_id, exc,
                )
                return None

        try:
            return await self.report_repo.find_latest_full(tenant_key)
        except Exception as exc:
            logger.warning("deep_report find_latest_full failed: tenant={} err={}", tenant_key, exc)
            return None


@dataclass(slots=True)
class DeepReportHandoffContractAttentionProvider:
    """Translate structured handoff contracts into one-turn execution attention."""

    source: str = "deep_report_handoff_contract"
    priority: int = 85
    placement: str = "before_last_user"

    async def collect_attention(
        self,
        ctx: ContextBuildContext,
    ) -> list[AttentionPacket]:
        contract = ctx.metadata.get("handoff_contract")
        if not isinstance(contract, dict) or not contract:
            return []
        if str(contract.get("kind") or "").strip() != "deep_report":
            return []

        intent = str(contract.get("intent") or "").strip() or "chat"
        required_tool = str(contract.get("required_tool") or "").strip()
        if required_tool != "deep_research":
            content = (
                "【当前深度报告转交】主 Agent 把用户这句话转交给深度报告助手继续回答。"
                f"当前意图是 {intent}。"
                "请基于已注入的用户画像、当前可用深度报告和用户原话回答。"
                "不要主动调用 `deep_research`。"
            )
            return [AttentionPacket(
                content=content,
                source=self.source,
                priority=self.priority,
                lifetime="one_turn",
                placement=self.placement,
                metadata={
                    "intent": intent,
                    "required_tool": required_tool,
                },
            )]

        forbid_claiming = bool(contract.get("forbid_claiming_completion_without_tool"))
        content = (
            "【当前深度报告工具任务】主 Agent 已判断用户明确需要生成、刷新或重生成深度分析报告。"
            f"当前意图是 {intent}。"
            "本轮优先目标：直接调用 `deep_research`。"
            "不要先展示已有深度报告，不要直接输出完整报告结论。"
        )
        if forbid_claiming:
            content += "在未调用 `deep_research` 前，不要说深度报告已经进入生成队列，也不要说报告已经生成。"
        content += "只有当工具返回 deferred/deduped/failed 时，才根据工具反馈自然回复。"

        return [AttentionPacket(
            content=content,
            source=self.source,
            priority=self.priority,
            lifetime="one_turn",
            placement=self.placement,
            metadata={
                "intent": intent,
                "required_tool": required_tool,
            },
        )]


def _plain_document(content: str) -> str:
    return content.strip()


def _format_soul_document(content: str) -> str:
    return (
        "【用户沟通偏好 / 红线 · SOUL.md】\n"
        "以下是该用户明确表达过的长期沟通偏好、硬拒或红线，本轮回复要遵守；"
        "若她自己重新起头某条红线，可以接，但不要绕回劝说：\n\n"
        + content.strip()
    )


def _format_deep_report_session_state(report: dict[str, Any]) -> str:
    report_id = str(report.get("report_id") or "").strip()
    suffix = f"报告 ID：{report_id}。" if report_id else ""
    return (
        "【深度报告会话状态】报告已完成，当前会话应解读这份报告。"
        f"{suffix}"
        "如果用户问报告里的问题，直接基于报告回答；"
        "不要再说正在安排、生成、调取报告，也不要主动收集生成前信息。"
    )


def _format_deep_report_freshness(report: dict[str, Any]) -> str:
    raw = str(report.get("create_time") or report.get("update_time") or "").strip()
    if not raw:
        return (
            "当前已读取到一份可用的深度分析报告。"
            "如果用户问报告内容/解读，可以基于这份报告回答；"
            "如果用户问生成进度、刷新或重新生成，必须以运行任务状态或工具返回为准，"
            "不要把这份报告当成新任务完成。"
        )

    parsed: datetime | None = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        return (
            f"当前可用深度分析报告生成时间：{raw}。"
            "如果用户问报告内容/解读，可以基于这份报告回答；"
            "如果用户问生成进度、刷新或重新生成，必须以运行任务状态或工具返回为准。"
        )

    days = (datetime.now().date() - parsed.date()).days
    if days <= 0:
        return (
            f"当前可用深度分析报告：今天生成（{raw}）。"
            "如果用户问报告内容/解读，可以基于这份报告回答；"
            "如果用户问生成进度、刷新或重新生成，必须以运行任务状态或工具返回为准，"
            "不要把这份报告当成新任务完成。"
        )

    freshness = "昨天生成" if days == 1 else f"{days}天前生成"
    return (
        f"当前可用深度分析报告：{freshness}（{raw}）。"
        "如果用户问报告内容/解读，可以说明这是历史报告并基于它回答；"
        "如果用户问今天最新状态、生成进度、刷新或重新生成，不要把这份历史报告说成当前新报告，"
        "请以运行任务状态或工具 gate 为准。"
    )


def _format_deep_report(report: dict[str, Any]) -> str:
    lines = ["【当前可用深度分析报告】"]
    report_id = str(report.get("report_id") or "").strip()
    if report_id:
        lines.append(f"报告 ID：{report_id}")
    if report.get("create_time"):
        lines.append(f"生成时间：{report['create_time']}")

    overview = _merge_overview(
        slow=report.get("slow_overview"),
        agent=report.get("agent_overview"),
    )
    decode = _merge_decode(
        slow=report.get("slow_decode"),
        deep=report.get("deep_decode"),
        agent=report.get("agent_decode"),
    )
    secret = _merge_secret(
        deep=report.get("deep_secret"),
        agent=report.get("agent_secret"),
    )

    radar_lines = _format_report_radar(overview)
    if radar_lines:
        lines.append("\n【五维状态】")
        lines.extend(radar_lines)

    skin_line = _format_report_skin_attribute(overview)
    if skin_line:
        lines.append("\n【肤质画像】")
        lines.append(skin_line)

    signal_lines = _format_report_signals(decode)
    if signal_lines:
        lines.append("\n【重点信号】")
        lines.extend(signal_lines)

    care_lines = _format_report_care_directions(secret)
    if care_lines:
        lines.append("\n【长期护理方向】")
        lines.extend(care_lines)

    return "\n".join(lines)


def _format_report_radar(overview: dict[str, Any]) -> list[str]:
    items = overview.get("radarDimensions")
    if not isinstance(items, list):
        return []

    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _short_text(item.get("name") or item.get("dimensionName") or item.get("dimensionCode"), limit=12)
        if not name:
            continue
        level = _short_text(
            item.get("status") or item.get("level") or item.get("score") or item.get("statusType"),
            limit=12,
        )
        image_desc = _short_text(item.get("imageDesc") or item.get("desc"), limit=28)
        parts = [part for part in (level, image_desc) if part]
        lines.append(f"- {name}：{'，'.join(parts)}" if parts else f"- {name}")
    return lines


def _format_report_skin_attribute(overview: dict[str, Any]) -> str:
    skin_attr = _coerce_dict(overview.get("skinAttribute"))
    if not skin_attr:
        return ""

    parts = [
        _short_attr(skin_attr.get("stage")),
        _short_attr(skin_attr.get("toneType") or skin_attr.get("tone")),
        _short_attr(skin_attr.get("oilType")),
        _short_text(skin_attr.get("oilDesc") or skin_attr.get("desc"), limit=28),
    ]
    parts = [part for part in parts if part]
    return f"- {'，'.join(parts)}" if parts else ""


def _format_report_signals(decode: dict[str, Any]) -> list[str]:
    items = decode.get("signals")
    if not isinstance(items, list):
        return []

    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = _short_text(item.get("name") or item.get("signalCode") or item.get("label"), limit=12)
        if not label:
            continue
        location = _short_text(
            item.get("locationText") or _join_list_text(item.get("areas"), sep="、"),
            limit=32,
        )
        detail, care = _signal_detail_and_care(item)
        parts = [part for part in (location, detail, care) if part]
        lines.append(f"- {label}：{'；'.join(parts)}" if parts else f"- {label}")
    return lines


def _format_report_care_directions(secret: dict[str, Any]) -> list[str]:
    mapping = (
        ("早上", secret.get("morningTitle")),
        ("晚上", secret.get("eveningTitle")),
        ("内调", secret.get("internalTitle")),
    )
    lines = []
    for label, value in mapping:
        text = _short_text(value, limit=36)
        if text:
            lines.append(f"- {label}：{text}")
    return lines


def _signal_detail_and_care(item: dict[str, Any]) -> tuple[str, str]:
    detail = _short_text(
        item.get("imageDesc")
        or item.get("aiLevelDetail")
        or item.get("levelDetail")
        or item.get("desc"),
        limit=30,
    )
    care = _short_text(
        item.get("careSuggestion")
        or item.get("care_suggestion")
        or item.get("suggestion")
        or item.get("care"),
        limit=22,
    )

    if detail and "；" in detail:
        left, _, right = detail.partition("；")
        detail = _short_text(left, limit=30)
        care = care or _short_text(right, limit=22)
    return detail, care


def _short_attr(value: Any) -> str:
    if isinstance(value, dict):
        return _short_text(value.get("name") or value.get("label") or value.get("value"), limit=16)
    return _short_text(value, limit=16)


def _join_list_text(value: Any, *, sep: str) -> str:
    if not isinstance(value, list):
        return ""
    texts = []
    for item in value:
        if isinstance(item, dict):
            text = item.get("label") or item.get("name") or item.get("area")
        else:
            text = item
        text = _short_text(text, limit=16)
        if text:
            texts.append(text)
    return sep.join(texts)


def _short_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    text = text.replace("✨", "").replace("～", "").strip()
    return text if len(text) <= limit else text[:limit]


def _merge_overview(*, slow: Any, agent: Any) -> dict[str, Any]:
    slow_o = _coerce_dict(slow)
    agent_o = _coerce_dict(agent)
    merged: dict[str, Any] = {}

    radar = _merge_dim_list(
        slow_o.get("radarDimensions"),
        agent_o.get("radarDimensions"),
        key="dimensionCode",
    )
    if radar:
        merged["radarDimensions"] = radar

    for key in ("introText",):
        value = agent_o.get(key)
        if not _is_empty(value):
            merged[key] = value

    signal = _merge_dict(slow_o.get("signal"), agent_o.get("signal"))
    if not _is_empty(signal):
        merged["signal"] = signal

    skin_attr = _merge_dict(slow_o.get("skinAttribute"), agent_o.get("skinAttribute"))
    if not _is_empty(skin_attr):
        merged["skinAttribute"] = skin_attr

    return merged


def _merge_decode(*, slow: Any, deep: Any, agent: Any) -> dict[str, Any]:
    slow_d = _coerce_dict(slow)
    deep_d = _coerce_dict(deep)
    agent_d = _coerce_dict(agent)

    merged: dict[str, Any] = {}
    banner = agent_d.get("bannerText")
    if not _is_empty(banner):
        merged["bannerText"] = banner

    signals = _merge_signals(
        slow_d.get("signals"),
        deep_d.get("signals"),
        agent_d.get("signals"),
    )
    if signals:
        merged["signals"] = signals
    return merged


def _merge_secret(*, deep: Any, agent: Any) -> dict[str, Any]:
    deep_s = _coerce_dict(deep)
    agent_s = _coerce_dict(agent)

    merged: dict[str, Any] = {}
    for key in ("introText", "focusTags", "morningTitle", "eveningTitle", "internalTitle"):
        value = agent_s.get(key)
        if not _is_empty(value):
            merged[key] = value
    for key in ("morningSteps", "eveningSteps", "internalSteps"):
        value = deep_s.get(key)
        if not _is_empty(value):
            merged[key] = value
    return merged


def _merge_track(*, slow: Any, agent: Any) -> dict[str, Any]:
    slow_t = _coerce_dict(slow)
    agent_t = _coerce_dict(agent)

    merged: dict[str, Any] = {}
    items = slow_t.get("signalItems")
    if not _is_empty(items):
        merged["signalItems"] = items
    day = agent_t.get("dayProgress")
    if not _is_empty(day):
        merged["dayProgress"] = day
    return merged


def _merge_dim_list(primary: Any, secondary: Any, *, key: str) -> list[dict[str, Any]]:
    primary_list = primary if isinstance(primary, list) else []
    secondary_list = secondary if isinstance(secondary, list) else []
    secondary_idx: dict[str, dict[str, Any]] = {}
    for item in secondary_list:
        if isinstance(item, dict) and item.get(key):
            secondary_idx[str(item[key])] = item

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in primary_list:
        if not isinstance(item, dict):
            continue
        code = str(item.get(key) or "")
        combined = dict(item)
        if code and code in secondary_idx:
            for k, v in secondary_idx[code].items():
                if not _is_empty(v):
                    combined[k] = v
            seen.add(code)
        merged.append(combined)
    for code, item in secondary_idx.items():
        if code not in seen:
            merged.append(dict(item))
    return merged


def _merge_signals(
    slow_signals: Any,
    deep_signals: Any,
    agent_signals: Any,
) -> list[dict[str, Any]]:
    def _index(items: Any) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        if not isinstance(items, list):
            return result
        for item in items:
            if isinstance(item, dict) and item.get("name"):
                result[str(item["name"])] = item
        return result

    slow_idx = _index(slow_signals)
    deep_idx = _index(deep_signals)
    agent_idx = _index(agent_signals)

    names: list[str] = []
    seen: set[str] = set()
    for src in (slow_signals, deep_signals, agent_signals):
        if not isinstance(src, list):
            continue
        for item in src:
            if isinstance(item, dict) and item.get("name"):
                name = str(item["name"])
                if name not in seen:
                    seen.add(name)
                    names.append(name)

    merged: list[dict[str, Any]] = []
    for name in names:
        combined: dict[str, Any] = {}
        for src in (slow_idx, deep_idx, agent_idx):
            payload = src.get(name) or {}
            for k, v in payload.items():
                if not _is_empty(v):
                    combined[k] = v
        analysis_imgs = combined.pop("analysisImages", None)
        if isinstance(analysis_imgs, list) and analysis_imgs:
            base_images = combined.get("images") if isinstance(combined.get("images"), list) else []
            combined["images"] = list(analysis_imgs) + list(base_images)
        merged.append(combined)
    return merged


def _merge_dict(primary: Any, secondary: Any) -> dict[str, Any]:
    primary_d = _coerce_dict(primary)
    secondary_d = _coerce_dict(secondary)
    merged: dict[str, Any] = {}
    for k, v in primary_d.items():
        if not _is_empty(v):
            merged[k] = v
    for k, v in secondary_d.items():
        if not _is_empty(v):
            merged[k] = v
    return merged


def _coerce_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _render_value(value: Any, *, depth: int = 0) -> list[str]:
    if depth > _MAX_DEPTH:
        return ["- ..."]
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if _is_empty(item):
                continue
            label = _label(key)
            if isinstance(item, (dict, list)):
                lines.append(f"- **{label}**")
                for child in _render_value(item, depth=depth + 1):
                    lines.append(f"  {child}")
            else:
                lines.append(f"- **{label}**：{_clip(str(item))}")
        return lines
    if isinstance(value, list):
        lines = []
        for idx, item in enumerate(value[:_MAX_LIST_ITEMS], start=1):
            if _is_empty(item):
                continue
            if isinstance(item, (dict, list)):
                lines.append(f"- 第 {idx} 项")
                for child in _render_value(item, depth=depth + 1):
                    lines.append(f"  {child}")
            else:
                lines.append(f"- {_clip(str(item))}")
        if len(value) > _MAX_LIST_ITEMS:
            lines.append(f"- ...（还有 {len(value) - _MAX_LIST_ITEMS} 项未展开）")
        return lines
    if isinstance(value, str):
        parsed = _try_json(value)
        if parsed is not value:
            return _render_value(parsed, depth=depth)
    return [f"- {_clip(str(value))}"]


def _task_status(task: dict[str, Any] | None) -> str:
    return normalize_status((task or {}).get("status"))


def _label(key: Any) -> str:
    text = str(key)
    return text.replace("_", " ").strip() or "字段"


def _clip(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + "..."


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return text
