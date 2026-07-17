"""深度分析工具 — 向深度报告服务发起异步生成请求。

DeepResearchTool 是耦合工具（needs_followup=True）：
LLM 调用后等待工具返回，Agent 可在第 2 轮向用户确认「报告生成中」。

执行流程：
  1. prepare_task() 构造 durable task
  2. ToolExecutionRuntime 统一 submit_task()
  3. 立即返回 {"ok": true, "action": "submitted", "runtime_task_status": "queued"}
  4. Worker 消费 deep_research stream → 发 HTTP → 写 nb_runtime_tasks

去重双层：
  1. 同 turn 内：_submitted_this_turn 内存标志，避免一次 LLM 调用里多次提交。
  2. 跨 turn 运行中任务：time_window_dedupe，只在 RuntimeTask 仍为 active 时短路。
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from simpleclaw.runtime.task_protocol import TaskEnvelope, make_trace_id
from simpleclaw.tools.base import Tool, ToolResult
from Mojing.runtime.streams import MojingTaskStream
from Mojing.runtime.task_types import MojingTaskType
from Mojing.runtime.tool_policies import time_window_dedupe
from Mojing.runtime.tool_results import tool_deduped, tool_submitted
from Mojing.storage.document_repo import DocumentRepository
from Mojing.storage.image_repo import ImageRepository
from Mojing.storage.runtime_task_repo import RuntimeTaskRepository

_MAX_USER_QUERY_CHARS = 4000
_DEFAULT_DEDUPE_WINDOW_S = 1800
_DEFAULT_ESTIMATED_TOTAL_MIN = 10


class DeepResearchTool(Tool):
    """Trigger a detailed skin analysis report for the user's current skin concern."""

    name = "deep_research"
    description = "Trigger a detailed skin analysis report for the user's current skin concern."
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    needs_followup = True
    execution_mode = "durable"
    tool_category = "async_task"
    durable_action = "submitted"

    def __init__(
        self,
        *,
        endpoint_url: str,
        document_repo: DocumentRepository,
        image_repo: ImageRepository | None = None,
        runtime_task_repo: RuntimeTaskRepository | None = None,
        dedupe_window_s: int = _DEFAULT_DEDUPE_WINDOW_S,
        estimated_total_min: int = _DEFAULT_ESTIMATED_TOTAL_MIN,
    ) -> None:
        self._endpoint_url = endpoint_url
        self._document_repo = document_repo
        self._image_repo = image_repo
        self._runtime_task_repo = runtime_task_repo
        self._dedupe_window_s = max(0, int(dedupe_window_s))
        self._estimated_total_min = max(1, int(estimated_total_min))
        self._tenant_key = "__default__"
        self._session_key = "cli:direct"
        self._origin_session_key = ""
        self._query = ""
        self._message_id: str | None = None
        self._handoff_contract: dict[str, Any] = {}
        self._submitted_this_turn = False

    def set_context(
        self,
        *,
        tenant_key: str = "",
        session_key: str = "",
        origin_session_key: str = "",
        query: str = "",
        message_id: str | None = None,
        handoff_contract: dict[str, Any] | None = None,
        **_,
    ) -> None:
        if tenant_key:
            self._tenant_key = tenant_key
        if session_key:
            self._session_key = session_key
        self._origin_session_key = str(origin_session_key or "").strip()
        self._query = query
        self._message_id = message_id
        self._handoff_contract = dict(handoff_contract or {})
        self._submitted_this_turn = False

    async def prepare_task(self, **_) -> TaskEnvelope | ToolResult:
        if self._submitted_this_turn:
            return tool_deduped(
                reason="already_submitted_in_turn",
                phase="already_submitted",
                source="turn_memory",
                message_focus=(
                    "本轮已经提交过深度报告生成任务，请沿用第一次工具结果回复用户。"
                    "不要重复触发，也不要说报告已经生成完成。"
                ),
            )

        dedupe = await self._maybe_dedupe_recent_request()
        if dedupe is not None:
            return dedupe

        # 加载用户画像
        user_profile = await self._document_repo.get(self._tenant_key, "USER.md") or ""

        parts: list[str] = []
        if self._query:
            parts.append(f"用户当前问题：{self._query}")
        if user_profile.strip():
            parts.append(f"用户画像：\n{user_profile.strip()}")
        user_query = "\n\n".join(parts)[:_MAX_USER_QUERY_CHARS].strip()

        if not self._tenant_key or not user_query:
            return ToolResult(
                content=json.dumps({"ok": False, "error": "missing user context"}, ensure_ascii=False),
                ok=False,
        )

        trace_id = make_trace_id()
        payload_user_id = str(self._tenant_key or "").strip()
        payload_session_id = await self._resolve_payload_session_id()
        payload = {
            "user_id":    payload_user_id,
            "session_id": payload_session_id,
            "user_query": user_query,
            "trace_id":   trace_id,
        }
        payload.update(_handoff_lineage_payload(self._handoff_contract))

        return TaskEnvelope(
            task_type=MojingTaskType.DEEP_RESEARCH,
            payload=payload,
            stream=MojingTaskStream.DEEP_RESEARCH,
            tenant_key=self._tenant_key,
            session_key=self._session_key,
            scope_key=f"{MojingTaskType.DEEP_RESEARCH}:{self._session_key}",
            trace_id=trace_id,
            service_role="mojing:deep-research",
        )

    async def on_task_submitted(self, task: TaskEnvelope, queue_id: str) -> None:
        self._submitted_this_turn = True
        logger.info(
            "deep_research queued: tenant={} session_key={} message_id={} task_id={} queue_id={}",
            self._tenant_key, self._session_key, self._message_id or "", task.task_id, queue_id,
        )

    def durable_result(self, task: TaskEnvelope, queue_id: str) -> ToolResult:
        where = "小程序「我的报告」页面"
        return tool_submitted(
            tool=self.name,
            task_id=task.task_id,
            queue_id=queue_id,
            estimated_minutes=self._estimated_total_min,
            where=where,
            message_focus=(
                f"深度报告已派发，预计 {self._estimated_total_min} 分钟内生成。"
                f"请告诉用户大概多久可以看到，以及去{where}查看。"
                "第一轮如果已经安抚过，这一轮不要重复安抚，重点说清楚落地信息。"
            ),
        )

    async def _maybe_dedupe_recent_request(self) -> ToolResult | None:
        if self._runtime_task_repo is None or not self._tenant_key:
            return None
        return await time_window_dedupe(
            runtime_task_repo=self._runtime_task_repo,
            tenant_key=self._tenant_key,
            task_type=MojingTaskType.DEEP_RESEARCH,
            dedupe_window_s=self._dedupe_window_s,
            estimated_total_min=self._estimated_total_min,
            in_progress_focus=lambda elapsed, remaining: (
                f"用户之前（约 {elapsed} 分钟前）已经触发过深度报告，"
                "当前任务状态仍显示在处理中。请告诉用户还在生成中，"
                "不要说报告已经好了，也不要重复说『我帮你触发』。"
            ),
        )

    async def _resolve_payload_session_id(self) -> str:
        """Use the main-agent session id for the external report request."""
        origin_session_key = str(self._origin_session_key or "").strip()
        if origin_session_key:
            if origin_session_key.startswith("main:"):
                return origin_session_key
            if ":" in origin_session_key:
                return f"main:{self._tenant_key}"
            return f"main:{origin_session_key}"
        session_key = str(self._session_key or "").strip()
        if session_key and not session_key.startswith("deep_report:"):
            if session_key.startswith("main:"):
                return session_key
            if ":" in session_key:
                return f"main:{self._tenant_key}"
            return f"main:{session_key}"
        return f"main:{self._tenant_key}"

    async def _latest_image_analysis_session_id(self) -> str:
        if self._image_repo is None or not self._tenant_key:
            return ""
        try:
            latest = await self._image_repo.find_latest_job(self._tenant_key)
        except Exception as exc:
            logger.warning(
                "deep_research resolve image session failed: tenant={} err={}",
                self._tenant_key,
                exc,
            )
            return ""
        return _image_job_external_session_id(latest)


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


def _image_job_external_session_id(job: dict[str, Any] | None) -> str:
    if not job:
        return ""
    request_payload = job.get("request_payload")
    if not isinstance(request_payload, dict):
        request_payload = {}

    nested_payload = request_payload.get("payload")
    if not isinstance(nested_payload, dict):
        nested_payload = {}

    candidates = (
        nested_payload.get("session_id"),
        request_payload.get("session_id"),
        nested_payload.get("origin_session_key"),
        request_payload.get("origin_session_key"),
        job.get("session_key"),
    )
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return ""
