"""Unified evidence retrieval tool for the main Agent."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from simpleclaw.tools.base import Tool, ToolResult

from Mojing.evidence import (
    EvidenceRoute,
    HistoricalImageRetriever,
    TextMemoryRetriever,
    route_evidence_query,
)

if TYPE_CHECKING:
    from simpleclaw.llm.base import LLMProvider
    from Mojing.storage.image_repo import ImageRepository
    from Mojing.storage.memory_repo import MySQLMemory


class RetrieveEvidenceTool(Tool):
    """Retrieve the right evidence for the user's current question.

    This is the model-visible entry point. Internally it delegates to text
    memory or historical image retrieval according to EvidenceRouter.
    """

    name = "retrieve_evidence"
    description = (
        "当用户的问题需要补充历史证据后再回答时调用。"
        "默认自动判断应取回文字记忆还是历史图片证据；"
        "如果业务 prompt 已经明确要求历史图片证据，可以把 route 设为 historical_image。"
        "适用于用户询问之前聊过/推荐过的内容，或要求查看之前照片、判断历史图片中的皮肤状态。"
        "不要用于泛泛护肤知识、普通闲聊，或当前上下文已经足够回答的情况。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "route": {
                "type": "string",
                "enum": ["auto", "text_memory", "historical_image"],
                "description": "证据类型。默认 auto；只有在 prompt 明确要求时才指定。",
                "default": "auto",
            },
        },
        "required": [],
    }
    needs_followup = True
    execution_mode = "inline"
    tool_category = "sync_read"
    business_ref_type = "evidence"
    business_ref_id_field = "job_id"

    def __init__(
        self,
        *,
        llm: "LLMProvider",
        memory: "MySQLMemory",
        image_repo: "ImageRepository",
    ) -> None:
        self._text_memory = TextMemoryRetriever(llm=llm, memory=memory)
        self._historical_image = HistoricalImageRetriever(image_repo=image_repo)
        self._tenant_key = "__default__"
        self._session_key = "cli:direct"
        self._origin_session_key = ""
        self._query = ""
        self._media: list[str] = []
        self._message_id: str | None = None
        self._route = route_evidence_query("")

    def set_context(
        self,
        *,
        tenant_key: str = "",
        session_key: str = "",
        origin_session_key: str = "",
        query: str = "",
        media: list[str] | None = None,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        if tenant_key:
            self._tenant_key = tenant_key
        if session_key:
            self._session_key = session_key
        self._origin_session_key = str(origin_session_key or "").strip()
        self._query = str(query or "").strip()
        self._media = [str(ref).strip() for ref in (media or []) if str(ref or "").strip()]
        self._message_id = message_id
        self._route = route_evidence_query(
            self._query,
            has_current_media=bool(self._media),
        )

        context = {
            "tenant_key": self._tenant_key,
            "session_key": self._session_key,
            "origin_session_key": self._origin_session_key,
            "query": self._query,
            "media": self._media,
            "message_id": self._message_id,
            **kwargs,
        }
        self._text_memory.set_context(
            **context,
            should_prefetch=self._route.kind == "text_memory",
        )
        self._historical_image.set_context(
            **context,
            should_prefetch=self._route.kind == "historical_image",
        )

    async def execute(self, route: str = "auto", **_) -> ToolResult:
        resolved_route = self._resolve_route(route)
        if resolved_route.kind == "text_memory":
            return await self._retrieve_text_memory(resolved_route)
        if resolved_route.kind == "historical_image":
            return await self._retrieve_historical_image(resolved_route)
        return _json_result({
            "ok": True,
            "action": "no_evidence_needed",
            "route": "none",
            "reason": resolved_route.reason,
            "message_focus": (
                "当前问题不需要额外召回历史证据。请基于当前上下文、用户画像和常识直接回答，"
                "不要编造不存在的历史信息。"
            ),
        })

    def _resolve_route(self, route: str = "auto") -> EvidenceRoute:
        explicit = str(route or "auto").strip()
        if explicit == "text_memory":
            if self._route.kind != "text_memory":
                self._text_memory.set_context(query=self._query, should_prefetch=True)
            return EvidenceRoute(kind="text_memory", reason="tool_argument")
        if explicit == "historical_image":
            if self._route.kind != "historical_image":
                self._historical_image.set_context(
                    tenant_key=self._tenant_key,
                    media=self._media,
                    should_prefetch=True,
                )
            return EvidenceRoute(kind="historical_image", reason="tool_argument")
        return self._route or route_evidence_query(self._query)

    async def _retrieve_text_memory(self, route: EvidenceRoute) -> ToolResult:
        content = (await self._text_memory.retrieve()).strip()
        return _json_result({
            "ok": True,
            "action": "evidence_retrieved",
            "route": "text_memory",
            "reason": route.reason,
            "evidence_type": "memory",
            "content": content,
            "matched_history": list(route.matched_history),
            "matched_text_memory": list(route.matched_text_memory),
            "message_focus": (
                "这是与用户当前问题相关的历史文字记忆证据。请先判断哪些内容能支撑当前问题，"
                "再用自然口吻整理回答；不要逐字复述记忆正文。"
                "如果内容为空或不相关，请诚实说明记不清，不要编造。"
            ),
        })

    async def _retrieve_historical_image(self, route: EvidenceRoute) -> ToolResult:
        payload = await self._historical_image.retrieve()
        payload["route"] = "historical_image"
        payload["evidence_type"] = "image"
        payload["reason"] = route.reason
        payload["matched_history"] = list(route.matched_history)
        payload["matched_visual"] = list(route.matched_visual)
        return _json_result(payload, ok=bool(payload.get("ok", True)))


def _json_result(payload: dict[str, Any], *, ok: bool = True) -> ToolResult:
    return ToolResult(content=json.dumps(payload, ensure_ascii=False), ok=ok)
