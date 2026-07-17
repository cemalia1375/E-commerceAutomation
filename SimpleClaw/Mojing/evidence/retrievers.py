"""Internal evidence retrievers.

These classes are not model-visible tools. They are implementation details
behind RetrieveEvidenceTool, keeping the public tool surface small while still
allowing text memory and historical image evidence to evolve independently.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from simpleclaw.llm.chunks import TextChunk

if TYPE_CHECKING:
    from simpleclaw.llm.base import LLMProvider
    from Mojing.storage.image_repo import ImageRepository
    from Mojing.storage.memory_repo import MySQLMemory


_MAX_RECALL_TOPICS = 3
_CONTENT_PREVIEW_CHARS = 180

_LITE_SELECT_PROMPT = """\
从以下历史记忆中，选出最能支撑回答用户当前问题的记忆编号（最多 {limit} 个）。
你看到的是索引摘要和正文预览，不是完整记忆；请优先选择能回答“之前说过什么、当时怎么约定、下次如何承接”的记忆。

选择标准：
- 用户问“昨天/之前/你说过/那个办法/继续吗”时，优先选择包含具体方案、用户反馈或下次承接方式的记忆。
- 只和用户问题泛泛相关、但不能支撑当前回答的记忆不要选。
- 没有相关记忆则返回 []。

只返回严格 JSON 数组，如 [1, 3]，没有相关话题则返回 []。不要 markdown，不要解释。

用户问题：{query}

话题列表：
{index}"""


class TextMemoryRetriever:
    """Prefetch and retrieve text memory evidence for a turn."""

    def __init__(
        self,
        *,
        llm: "LLMProvider",
        memory: "MySQLMemory",
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._query = ""
        self._prefetch_task: asyncio.Task | None = None

    def set_context(self, *, query: str = "", should_prefetch: bool = False, **_) -> None:
        self._query = str(query or "")
        self._prefetch_task = None
        if self._query and should_prefetch:
            logger.info("text evidence prefetch start: query={!r}", self._query[:60])
            self._prefetch_task = asyncio.create_task(self._prefetch(self._query))

    async def retrieve(self) -> str:
        if self._prefetch_task is None:
            logger.info("text evidence retrieve: no prefetch task")
            return ""
        prefetch_done = self._prefetch_task.done()
        logger.info("text evidence retrieve: awaiting prefetch (already_done={})", prefetch_done)
        try:
            recalled = await self._prefetch_task
        except Exception as exc:
            logger.warning("text evidence prefetch failed: {}", exc)
            return "（记忆召回失败，请直接根据已知信息回答）"
        logger.info(
            "text evidence retrieve: result_len={} content_preview={!r}",
            len(recalled or ""),
            (recalled or "")[:80],
        )
        return recalled or "（未找到相关历史记忆）"

    async def _prefetch(self, query: str) -> str:
        all_items = await self._memory.retrieve(top_k=20)
        logger.info("text evidence prefetch: memory_items_fetched={}", len(all_items))
        if not all_items:
            return ""

        kw_task = asyncio.create_task(_keyword_match_async(query, all_items))
        lm_task = asyncio.create_task(self._lite_model_select(query, all_items))
        kw_ids, lm_ids = await asyncio.gather(kw_task, lm_task)
        logger.info(
            "text evidence prefetch: kw_ids={} lm_ids={} using={}",
            kw_ids,
            lm_ids,
            "lm" if lm_ids else "kw",
        )

        chosen_ids = lm_ids if lm_ids else kw_ids
        if not chosen_ids:
            logger.info("text evidence prefetch: no items selected")
            return ""

        chosen_items = [all_items[i] for i in chosen_ids if i < len(all_items)]
        if not chosen_items:
            return ""

        logger.info("text evidence prefetch: selected_keys={}", [item.key for item in chosen_items])
        parts = ["# 相关历史记忆"]
        for item in chosen_items:
            description = str(item.description or "").strip()
            content = str(item.content or "").strip()
            body = [f"## {item.key}"]
            if description:
                body.append(f"召回理由：{description}")
            if content:
                body.append(f"记忆内容：\n{content}")
            parts.append("\n\n".join(body))
        return "\n\n".join(parts)

    async def _lite_model_select(self, query: str, items: list) -> list[int]:
        index_lines = "\n".join(
            _format_memory_index_item(i + 1, item)
            for i, item in enumerate(items)
        )
        prompt = _LITE_SELECT_PROMPT.format(
            limit=_MAX_RECALL_TOPICS,
            query=query,
            index=index_lines,
        )
        logger.debug("text evidence lite_select prompt index:\n{}", index_lines)
        raw = ""
        try:
            async for chunk in self._llm.stream_with_retry(
                [{"role": "user", "content": prompt}],
                max_tokens=64,
                temperature=0.0,
            ):
                if isinstance(chunk, TextChunk):
                    raw += chunk.token
            raw = raw.strip()
            logger.info("text evidence lite_select raw_output={!r}", raw)
            result = json.loads(raw)
            if isinstance(result, list):
                return [int(x) - 1 for x in result if isinstance(x, int) and x >= 1]
        except Exception as exc:
            logger.warning("text evidence lite_select failed: {} raw={!r}", exc, raw[:80])
        return []


class HistoricalImageRetriever:
    """Retrieve the latest usable historical image for a tenant."""

    def __init__(self, *, image_repo: "ImageRepository") -> None:
        self._image_repo = image_repo
        self._tenant_key = "__default__"
        self._media: list[str] = []
        self._prefetch_task: asyncio.Task | None = None

    def set_context(
        self,
        *,
        tenant_key: str = "",
        media: list[str] | None = None,
        should_prefetch: bool = False,
        **_,
    ) -> None:
        if tenant_key:
            self._tenant_key = tenant_key
        self._media = [str(ref).strip() for ref in (media or []) if str(ref or "").strip()]
        self._prefetch_task = None
        if should_prefetch:
            logger.info(
                "historical image evidence prefetch start: tenant={} exclude_refs_n={}",
                self._tenant_key,
                len(self._media),
            )
            self._prefetch_task = asyncio.create_task(self._lookup())

    async def retrieve(self) -> dict[str, Any]:
        if self._prefetch_task is not None:
            prefetch_done = self._prefetch_task.done()
            logger.info("historical image evidence retrieve: awaiting prefetch (already_done={})", prefetch_done)
            try:
                return await self._prefetch_task
            except Exception as exc:
                logger.warning("historical image evidence prefetch failed: {}", exc)
                return {
                    "ok": True,
                    "action": "image_unavailable",
                    "reason": "image_lookup_failed",
                    "message_focus": (
                        "历史图片查询暂时不可用。请不要声称已经看过图片，"
                        "先根据已知画像和当前对话正常回答。"
                    ),
                }
        return await self._lookup()

    async def _lookup(self) -> dict[str, Any]:
        tenant_key = str(self._tenant_key or "").strip()
        if not tenant_key or tenant_key == "__default__":
            return {
                "ok": True,
                "action": "image_unavailable",
                "reason": "missing_tenant",
                "message_focus": (
                    "当前无法定位用户的历史图片。请不要声称已经看过图片，"
                    "直接正常聊天；如果用户需要看图判断，再温柔引导她重新上传。"
                ),
            }

        try:
            record = await self._image_repo.get_latest_succeeded_record_excluding(
                tenant_key,
                exclude_refs=self._media,
            )
        except Exception as exc:
            logger.warning("historical image evidence lookup failed: tenant={} err={}", tenant_key, exc)
            return {
                "ok": True,
                "action": "image_unavailable",
                "reason": "image_lookup_failed",
                "message_focus": (
                    "历史图片查询暂时不可用。请不要声称已经看过图片，"
                    "先根据已知画像和当前对话正常回答。"
                ),
            }

        image_url = str((record or {}).get("image_ref") or "").strip()
        if not image_url:
            return {
                "ok": True,
                "action": "image_unavailable",
                "reason": "skin_image_record_unavailable",
                "message_focus": (
                    "没有找到已完成图片分析的历史皮肤照。请不要声称已经看过历史图片，"
                    "先根据已知画像和当前对话正常回答；如果用户需要看图判断，再温柔引导她重新上传。"
                ),
            }

        excluded_current_turn = bool(self._media)
        source = "latest_before_current_turn" if excluded_current_turn else "latest_known"
        return {
            "ok": True,
            "action": "image_fetched",
            "image_url": image_url,
            "image_id": str(record.get("image_id") or ""),
            "job_id": str(record.get("job_id") or ""),
            "uploaded_at": _format_datetime(record.get("created_at")),
            "image_status": str(record.get("status") or ""),
            "source": source,
            "excluded_current_turn": excluded_current_turn,
            "message_focus": (
                "已取到一张历史皮肤照；可基于它回答当前问题，"
                "但不要说成本轮新拍或新检测结论。"
            ),
        }


async def _keyword_match_async(query: str, items: list) -> list[int]:
    return _keyword_match(query, items)


def _format_memory_index_item(index: int, item: Any) -> str:
    description = str(getattr(item, "description", "") or "").strip()
    content = str(getattr(item, "content", "") or "").strip()
    preview = _content_preview(content)
    lines = [
        f"{index}. {getattr(item, 'key', '')}",
        f"description: {description or preview}",
    ]
    if preview:
        lines.append(f"content_preview: {preview}")
    return "\n".join(lines)


def _content_preview(content: str, *, limit: int = _CONTENT_PREVIEW_CHARS) -> str:
    text = " ".join(str(content or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _keyword_match(query: str, items: list) -> list[int]:
    scored: list[tuple[int, int]] = []
    for i, item in enumerate(items):
        haystack = " ".join([
            str(getattr(item, "key", "") or ""),
            str(getattr(item, "description", "") or ""),
            _content_preview(str(getattr(item, "content", "") or "")),
        ]).lower()
        tokens = _tokenize(haystack)
        hits = sum(1 for t in tokens if t in query)
        if hits:
            scored.append((i, hits))
    scored.sort(key=lambda x: -x[1])
    return [i for i, _ in scored[:_MAX_RECALL_TOPICS]]


def _tokenize(text: str) -> list[str]:
    tokens = text.split()
    tokens += [text[j: j + 2] for j in range(len(text) - 1)]
    return tokens


def _format_datetime(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)
