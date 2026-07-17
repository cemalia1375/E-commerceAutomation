"""DeepReportChatTool — 主 Agent 向深度报告子 Agent 派发任务。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from loguru import logger

from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.tools.base import Tool, ToolResult
from Mojing.runtime.streams import MojingTaskStream
from Mojing.runtime.task_types import MojingTaskType
from Mojing.runtime.tool_results import tool_submitted

if TYPE_CHECKING:
    from Mojing.storage.image_repo import ImageRepository
    from Mojing.storage.runtime_task_repo import RuntimeTaskRepository


_STALE_SELFIE_AFTER = timedelta(hours=8)


class DeepReportChatTool(Tool):
    """向深度报告子 Agent 发送任务消息（非阻塞，后台执行）。"""

    name = "deep_report_chat"
    description = (
        "把深度分析报告相关问题转交给深度报告助手。"
        "用户明确要求生成、刷新、重新生成深度报告时，调用本工具并传 intent=handoff。"
        "用户只是询问、解读或继续聊已有深度报告时，传 intent=chat。"
        "调用后立即返回，不等待子 Agent 完成；用户原话会自动转发。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": ["chat", "handoff"],
                "description": (
                    "chat=转交深度报告助手基于已有报告继续解释或回答；"
                    "handoff=用户明确要求生成、刷新、重生成深度分析报告。"
                ),
                "default": "chat",
            },
            "allow_stale_selfie": {
                "type": "boolean",
                "description": (
                    "仅当工具曾提示自拍已超过时效，且用户明确表示不补新自拍、同意沿用之前图片时设为 true。"
                    "默认 false；不要为了省事主动设为 true。"
                ),
                "default": False,
            },
        },
        "required": [],
    }

    needs_followup = True
    execution_mode = "durable"
    tool_category = "async_task"

    def __init__(
        self,
        *,
        image_repo: "ImageRepository | None" = None,
        runtime_task_repo: "RuntimeTaskRepository | None" = None,
    ) -> None:
        self._image_repo = image_repo
        self._runtime_task_repo = runtime_task_repo
        self._tenant_key = "__default__"
        self._origin_session_key = ""
        self._query = ""
        self._media: list[str] = []

    def set_context(
        self,
        *,
        tenant_key: str = "",
        session_key: str = "",
        query: str = "",
        media: list[str] | None = None,
        **_,
    ) -> None:
        if tenant_key:
            self._tenant_key = tenant_key
        self._origin_session_key = str(session_key or "").strip()
        self._query = str(query or "").strip()
        self._media = list(media or [])

    async def prepare_task(self, **params) -> TaskEnvelope | ToolResult:
        query = self._query or str(params.get("task") or "").strip()
        if not query:
            return ToolResult(
                content=json.dumps({"ok": False, "error": "missing user query"}, ensure_ascii=False),
                ok=False,
            )

        intent = _normalize_intent(params.get("intent"))
        if intent == "handoff":
            stale_block = await self._maybe_block_stale_selfie(
                allow_stale_selfie=bool(params.get("allow_stale_selfie")),
            )
            if stale_block is not None:
                return stale_block

        session_key = f"deep_report:{self._tenant_key}"
        handoff_contract: dict[str, object] = {
            "kind": "deep_report",
            "intent": intent,
        }
        if intent == "handoff":
            handoff_contract.update({
                "required_tool": "deep_research",
                "allow_natural_reply_only_on": ["deferred", "deduped", "failed"],
                "forbid_claiming_completion_without_tool": True,
            })
        dispatch_message = _handoff_message(query) if intent == "handoff" else query
        return TaskEnvelope(
            task_type=MojingTaskType.SUBAGENT_DISPATCH,
            payload={
                "session_key": session_key,
                "tenant_key": self._tenant_key,
                "message": dispatch_message,
                "user_query": dispatch_message,
                "original_user_query": query,
                "action_key": "deep_report.handoff",
                "source": "deep_report_chat",
                "origin_session_key": self._origin_session_key,
                "handoff_contract": handoff_contract,
            },
            stream=MojingTaskStream.SUBAGENT_DISPATCH,
            tenant_key=self._tenant_key,
            session_key=session_key,
            scope_key=f"{MojingTaskType.SUBAGENT_DISPATCH}:{session_key}",
            service_role="mojing:subagent-dispatch",
        )

    async def on_task_submitted(self, task: TaskEnvelope, queue_id: str) -> None:
        logger.info(
            "deep_report_chat queued: tenant={} session_key={} queue_id={}",
            self._tenant_key,
            task.session_key,
            queue_id,
        )

    def durable_result(self, task: TaskEnvelope, queue_id: str) -> ToolResult:
        contract = task.payload.get("handoff_contract") if isinstance(task.payload, dict) else {}
        intent = str((contract or {}).get("intent") or "chat")
        if intent == "handoff":
            message_focus = (
                "已把生成或刷新深度分析报告的请求交给深度报告助手。"
                "请告诉用户报告正在处理，完成后去「我的报告」页面查看。"
                "不要说报告已经生成完成，也不要复述具体报告结论。"
            )
        else:
            message_focus = (
                "已把这个深度报告相关问题交给深度分析报告助手。"
                "请告诉用户去深度分析报告助手会话框中继续沟通细节。"
            )
        return tool_submitted(
            tool=self.name,
            task_id=task.task_id,
            queue_id=queue_id,
            subagent="deep_report",
            where="深度分析报告会话框",
            message_focus=message_focus,
        )

    async def _maybe_block_stale_selfie(self, *, allow_stale_selfie: bool) -> ToolResult | None:
        if self._media:
            return None
        if allow_stale_selfie:
            return None
        if self._runtime_task_repo is None:
            return None

        latest = await self._latest_succeeded_image_analysis_task()
        if latest is None:
            return _selfie_block_result(
                reason="missing_selfie",
                message_focus=(
                    "当前没有可用的已完成自拍/肤况分析。不要说深度报告已经提交。"
                    "请告诉用户：为了生成新的深度分析报告，需要先补一张当前清晰自拍。"
                ),
                model_guidance=(
                    "本次 deep_report_chat 没有派发，因为没有找到已成功完成的自拍/肤况分析任务。"
                    "请先引导用户上传当前清晰自拍，等图片分析完成后再生成深度报告。"
                ),
            )

        last_time = await self._image_analysis_reference_time(latest)
        if last_time is None:
            return None

        now = datetime.now(timezone.utc) if last_time.tzinfo is not None else datetime.utcnow()
        delta = now - last_time if last_time.tzinfo is not None else now - last_time.replace(tzinfo=None)
        if delta < _STALE_SELFIE_AFTER:
            return None

        age = _format_age(delta)
        return _selfie_block_result(
            reason="stale_selfie",
            stale_selfie_age=age,
            source_task_id=str(latest.get("task_id") or "").strip() or None,
            message_focus=(
                f"用户上次自拍/肤况图是{age}。不要说深度报告已经提交。"
                "请告诉用户：为了让新的深度分析报告更贴近当前状态，建议先补一张当前清晰自拍；"
                "如果现在不方便，也可以明确说沿用之前那张。"
            ),
            model_guidance=(
                "本次 deep_report_chat 没有派发，因为当前可用自拍已超过时效。"
                "先让用户选择补新图或沿用旧图；用户明确沿用旧图后，可以再次调用 "
                "deep_report_chat(intent=\"handoff\", allow_stale_selfie=true)。"
            ),
        )

    async def _latest_succeeded_image_analysis_task(self) -> dict[str, Any] | None:
        if self._runtime_task_repo is None:
            return None
        finder = getattr(self._runtime_task_repo, "find_latest_succeeded_task_for", None)
        if not callable(finder):
            return None
        try:
            return await finder(
                tenant_key=self._tenant_key,
                task_type=MojingTaskType.IMAGE_ANALYSIS,
            )
        except Exception as exc:
            logger.warning("deep_report_chat image source lookup failed: tenant={} err={}", self._tenant_key, exc)
            return None

    async def _image_analysis_reference_time(self, task: dict[str, Any]) -> datetime | None:
        payload = dict((task or {}).get("payload") or {})
        job_id = str(payload.get("job_id") or "").strip()
        if job_id and self._image_repo is not None:
            getter = getattr(self._image_repo, "get_job_by_id", None)
            if callable(getter):
                try:
                    job = await getter(self._tenant_key, job_id)
                except Exception as exc:
                    logger.warning(
                        "deep_report_chat image job lookup failed: tenant={} job_id={} err={}",
                        self._tenant_key,
                        job_id,
                        exc,
                    )
                    job = None
                job_time = _task_time(dict(job or {}), "completed_at", "updated_at", "created_at")
                if job_time is not None:
                    return job_time
        return _task_time(task, "completed_at", "updated_at", "created_at")


def _normalize_intent(value: object) -> str:
    text = str(value or "").strip()
    if text == "handoff":
        return "handoff"
    return "chat"


def _handoff_message(user_query: str) -> str:
    text = str(user_query or "").strip()
    if not text:
        text = "用户已同意生成新的深度分析报告。"
    return (
        "【主 Agent 转交任务】\n"
        "用户已明确要求或同意生成、刷新、重新生成一份新的深度分析报告。\n"
        f"用户原话：{text}\n\n"
        "请本轮直接调用 deep_research 发起生成。\n"
        "不要把已有旧报告当成本次任务完成；不要自然回复“等报告出来”，"
        "除非 deep_research 工具已经返回 submitted、deduped、deferred 或 failed。"
    )


def _selfie_block_result(
    *,
    reason: str,
    message_focus: str,
    model_guidance: str,
    stale_selfie_age: str | None = None,
    source_task_id: str | None = None,
) -> ToolResult:
    payload = {
        "ok": True,
        "action": "blocked",
        "reason": reason,
        "phase": "needs_user_confirmation",
        "runtime_task_created": False,
        "message_focus": message_focus,
        "model_guidance": model_guidance,
    }
    if stale_selfie_age:
        payload["stale_selfie_age"] = stale_selfie_age
    if source_task_id:
        payload["source_task_id"] = source_task_id
    return ToolResult(content=json.dumps(payload, ensure_ascii=False), ok=True)


def _task_time(task: dict[str, Any], *keys: str) -> datetime | None:
    for key in keys:
        parsed = _coerce_datetime(task.get(key))
        if parsed is not None:
            return parsed
    return None


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _format_age(delta: timedelta) -> str:
    total_seconds = max(0, int(delta.total_seconds()))
    hours = total_seconds // 3600
    days = hours // 24
    if days > 0:
        rest_hours = hours % 24
        if rest_hours:
            return f"约{days}天{rest_hours}小时前"
        return f"约{days}天前"
    if hours > 0:
        return f"约{hours}小时前"
    minutes = max(1, total_seconds // 60)
    return f"约{minutes}分钟前"
