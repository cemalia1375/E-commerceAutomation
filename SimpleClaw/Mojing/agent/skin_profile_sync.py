"""把 n8n 产出的皮肤画像同步进 USER.md。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from loguru import logger

from Mojing.storage.document_repo import DocumentRepository
from Mojing.storage.image_repo import ImageRepository
from Mojing.storage.skin_profile_repo import SkinProfileRepository
from Mojing.storage.tenant_state_repo import TenantStateRepository
from Mojing.utils.skin_signals import (
    severity_rank,
    signal_care_suggestions,
    signal_label,
    signal_location_text,
    signal_severity,
)


_SKIN_SECTION_HEADING = "## Learned Skin Profile"
_BLOCK_NAME = "Learned Skin Profile"
_WRITER_TAG = "skin_profile_sync"


class SyncOutcome(str, Enum):
    NO_PENDING = "no_pending"
    FIRST_SEED = "first_seed"
    SELF_UPDATE = "self_update"
    OVERWRITE = "overwrite"
    NO_CHANGE = "no_change"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SyncResult:
    outcome: SyncOutcome
    profile_id: int | None = None
    detail: str = ""


def extract_skin_summary(row: dict[str, Any], *, parse_json: Any = None) -> dict[str, str]:
    """从 nb_tenant_skin_profiles 行提取 USER.md 需要的扁平摘要。"""
    if parse_json is None:
        parse_json = _parse_json_field

    attr = parse_json(row.get("skin_attribute_json")) or {}
    signals = parse_json(row.get("signals_json")) or []
    advantages = parse_json(row.get("advantages_json")) or []

    skin_stage = _deep_get(attr, "stage", "name") or ""
    skin_tone_type = _deep_get(attr, "toneType", "name") or ""
    skin_type = _deep_get(attr, "oilType", "name") or ""
    skin_concern = _aggregate_signal_codes(signals)
    concern_distribution = _aggregate_signal_distribution(signals)
    overall_state = str(row.get("overall_state") or "")
    skin_advantages = "、".join(advantages) if isinstance(advantages, list) else str(advantages)
    care_focus = _aggregate_care_suggestions(signals)
    seeded_at = _format_date(row.get("created_at"))

    return {
        "skin_stage": skin_stage,
        "skin_tone_type": skin_tone_type,
        "skin_type": skin_type,
        "skin_concern": skin_concern,
        "skin_concern_distribution": concern_distribution,
        "skin_overall_state": overall_state,
        "skin_advantages": skin_advantages,
        "skin_care_focus": care_focus,
        "skin_profile_seeded_at": seeded_at,
    }


def render_skin_block(summary: dict[str, str]) -> str:
    """把皮肤画像摘要渲染成 USER.md 中的 markdown block body。"""
    lines = [
        f"- 肤龄阶段：{summary.get('skin_stage') or '未知'}",
        f"- 肤色调：{summary.get('skin_tone_type') or '未知'}",
        f"- 肤质：{summary.get('skin_type') or '未知'}",
        f"- 主要肤况：{summary.get('skin_concern') or '暂无'}",
        f"- 问题分布：{summary.get('skin_concern_distribution') or '暂无'}",
        f"- 皮肤总评：{summary.get('skin_overall_state') or '暂无'}",
        f"- 皮肤优势：{summary.get('skin_advantages') or '暂无'}",
        f"- 护理关注点：{summary.get('skin_care_focus') or '暂无'}",
        f"- 最近图片建档时间：{summary.get('skin_profile_seeded_at') or '未知'}",
    ]
    return "\n".join(lines)


def extract_existing_block(content: str) -> str | None:
    """提取 USER.md 中现有的 Learned Skin Profile block body。"""
    match = _skin_section_match(content)
    if not match:
        return None
    text = match.group(0)
    body = text.split("\n", 1)[1] if "\n" in text else ""
    return body.strip()


def merge_skin_section(content: str, body: str) -> str:
    """插入或替换 USER.md 中的 Learned Skin Profile section。"""
    original = content.strip()
    block = f"{_SKIN_SECTION_HEADING}\n\n{body.strip()}"
    if not original:
        return block

    replace_pat = _skin_section_pattern()
    if re.search(replace_pat, original):
        updated = re.sub(
            replace_pat,
            lambda match: f"{_SKIN_SECTION_HEADING}\n\n{body.strip()}",
            original,
            count=1,
        )
        return updated.strip()
    return f"{original}\n\n{block}".strip()


def _skin_section_match(content: str) -> re.Match[str] | None:
    return re.search(_skin_section_pattern(), content.strip())


def _skin_section_pattern() -> str:
    heading_esc = re.escape(_SKIN_SECTION_HEADING)
    return rf"(?m)^{heading_esc}[^\n]*(?:\n(?!##\s+).*)*"


class SkinProfileSyncer:
    """同步一条 pending 皮肤画像到 USER.md。"""

    def __init__(
        self,
        *,
        skin_repo: SkinProfileRepository,
        document_repo: DocumentRepository,
        image_repo: ImageRepository | None = None,
        tenant_state_repo: TenantStateRepository | None = None,
    ) -> None:
        self._skin_repo = skin_repo
        self._document_repo = document_repo
        self._tenant_state_repo = tenant_state_repo
        # image job 状态只表达图片分析本身；USER.md 同步由 skin_profile_sync task
        # 和 nb_tenant_skin_profiles.sync_status 单独表达。
        del image_repo

    async def sync(self, tenant_key: str) -> SyncResult:
        row = await self._skin_repo.find_pending(tenant_key)
        if row is None:
            return SyncResult(SyncOutcome.NO_PENDING)

        profile_id = int(row["profile_id"])
        try:
            return await self._do_sync(tenant_key, profile_id, row)
        except Exception as exc:
            logger.opt(exception=True).warning(
                "skin_profile_sync failed: tenant={} profile={}",
                tenant_key, profile_id,
            )
            await self._skin_repo.mark_failed(profile_id, error=str(exc))
            return SyncResult(SyncOutcome.FAILED, profile_id=profile_id, detail=str(exc))

    async def _do_sync(
        self,
        tenant_key: str,
        profile_id: int,
        row: dict[str, Any],
    ) -> SyncResult:
        summary = extract_skin_summary(row, parse_json=self._skin_repo.parse_json_field)
        new_body = render_skin_block(summary)
        new_hash = _content_hash(new_body)

        current_doc = await self._document_repo.get(tenant_key, "USER.md") or ""
        existing_body = extract_existing_block(current_doc)

        if existing_body is None or not existing_body.strip():
            outcome = SyncOutcome.FIRST_SEED
        elif _content_hash(existing_body) == new_hash:
            await self._skin_repo.mark_skipped(profile_id, sync_reason="no_change")
            return SyncResult(SyncOutcome.NO_CHANGE, profile_id=profile_id)
        else:
            meta = await self._skin_repo.get_block_meta(tenant_key, _BLOCK_NAME)
            if meta and meta.get("content_hash") == _content_hash(existing_body):
                outcome = SyncOutcome.SELF_UPDATE
            else:
                outcome = SyncOutcome.OVERWRITE

        updated_doc = merge_skin_section(current_doc, new_body)
        await self._document_repo.set(tenant_key, "USER.md", updated_doc)

        await self._skin_repo.mark_synced(profile_id, sync_reason=outcome.value)
        await self._skin_repo.upsert_block_meta(
            tenant_key=tenant_key,
            block_name=_BLOCK_NAME,
            last_writer=_WRITER_TAG,
            last_profile_id=profile_id,
            content_hash=new_hash,
        )

        logger.info(
            "skin_profile_sync done: tenant={} profile={} outcome={}",
            tenant_key, profile_id, outcome.value,
        )
        return SyncResult(outcome, profile_id=profile_id)


def _parse_json_field(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _deep_get(d: dict, *keys: str) -> Any:
    for key in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(key)  # type: ignore[assignment]
    return d


def _aggregate_signal_codes(signals: list[dict]) -> str:
    by_code: dict[str, str] = {}
    for signal in signals:
        label = signal_label(signal)
        if not label:
            continue
        severity = signal_severity(signal)
        previous = by_code.get(label, "")
        if not previous or severity_rank(severity) > severity_rank(previous):
            by_code[label] = severity
    return "、".join(_signal_label(label, severity) for label, severity in by_code.items()) if by_code else ""


def _aggregate_signal_distribution(signals: list[dict]) -> str:
    grouped: dict[str, list[str]] = {}
    for signal in signals:
        location = signal_location_text(signal)
        label = signal_label(signal)
        if not location or not label:
            continue
        label = _signal_label(label, signal_severity(signal))
        bucket = grouped.setdefault(location, [])
        if label not in bucket:
            bucket.append(label)
    if not grouped:
        return ""
    return "；".join(f"{location}：{'、'.join(codes)}" for location, codes in grouped.items())


def _signal_label(code: str, severity: str) -> str:
    return f"{code}（{severity}）" if severity else code


def _aggregate_care_suggestions(signals: list[dict]) -> str:
    seen: set[str] = set()
    suggestions: list[str] = []
    for signal in signals:
        for tip in signal_care_suggestions(signal):
            if tip and tip not in seen:
                seen.add(tip)
                suggestions.append(tip)
    return "、".join(suggestions) if suggestions else ""


def _format_date(dt: Any) -> str:
    if dt is None:
        return ""
    if hasattr(dt, "strftime"):
        return dt.strftime("%Y-%m-%d")
    return str(dt)[:10]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()
