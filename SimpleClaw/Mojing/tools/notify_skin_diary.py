"""NotifySkinDiaryChatTool — 主 Agent 向肌肤日记子 Agent 发送任务通知。

这是会回 ack 的 trigger 工具（needs_followup=True）：
主 Agent 调用此工具后会先拿到“已派发”的工具结果，再继续回复用户；
子 Agent 的真实执行发生在后台 worker 中，不阻塞主 Agent。

子 Agent 的 session_key 直接由 tenant_key 派生（"skin_diary:{tenant_key}"），
不需要查询额外的 DB 表，隔离通过 session_key 前缀实现。

触发时机（主 Agent 系统提示词应声明）：
  - 用户主动询问肌肤日记或想查看皮肤分析时
  - 图片分析完成后，provider 注入首次肌肤日记路由事实时
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.tools.base import Tool, ToolResult
from Mojing.runtime.streams import MojingTaskStream
from Mojing.runtime.task_types import MojingTaskType
from Mojing.runtime.tool_results import tool_submitted


_STALE_SELFIE_AFTER = timedelta(hours=8)


class NotifySkinDiaryChatTool(Tool):
    """向肌肤日记子 Agent 发送任务消息（非阻塞，后台执行）。"""

    name = "notify_skin_diary_chat"
    description = (
        "把用户关于肌肤日记、皮肤状态、皮肤分析结果的问题转交给肌肤日记助手。"
        "调用后立即返回，不等待子 Agent 完成。用户原话会被自动转发，无需你改写。"
        "适用场景：用户想查看、解释、继续聊或刷新肌肤日记。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": ["chat", "handoff"],
                "description": (
                    "chat=转交肌肤日记助手基于已有日记继续回答；"
                    "handoff=主 Agent 明确派肌肤日记助手触发生成、刷新、更新或重生成。"
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

    needs_followup = True  # 工具立即返回 stub，模型拿着 ack 在第 2 轮向用户说话
    execution_mode = "durable"
    tool_category = "async_task"

    def __init__(self, *, runtime_task_repo: Any | None = None, image_repo: Any | None = None) -> None:
        self._runtime_task_repo = runtime_task_repo
        self._image_repo = image_repo
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
        """由 SessionStore.set_turn_context() 在每轮开始时调用。"""
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

        # session_key 直接派生，不需要查 DB
        skin_session_key = f"skin_diary:{self._tenant_key}"
        handoff_contract: dict[str, object] = {
            "kind": "skin_diary",
            "intent": intent,
        }
        if intent == "handoff":
            handoff_contract.update({
                "required_tool": "generate_skin_diary",
                "show_existing_first": False,
                "allow_natural_reply_only_on": ["deferred", "deduped", "failed"],
                "forbid_claiming_completion_without_tool": True,
            })
        source = await self._current_image_analysis_source()
        payload = {
            "session_key": skin_session_key,
            "tenant_key": self._tenant_key,
            "message": query,
            "user_query": query,
            "action_key": "skin_diary.handoff",
            "source": "notify_skin_diary_chat",
            "origin_session_key": self._origin_session_key,
            "handoff_contract": handoff_contract,
        }
        if source.get("source_task_id"):
            payload["source_task_id"] = source["source_task_id"]
        if source.get("source_image_id"):
            payload["source_image_id"] = source["source_image_id"]
        if source.get("source_image_ref"):
            payload["source_image_ref"] = source["source_image_ref"]
        return TaskEnvelope(
            task_type=MojingTaskType.SUBAGENT_DISPATCH,
            payload=payload,
            stream=MojingTaskStream.SUBAGENT_DISPATCH,
            tenant_key=self._tenant_key,
            session_key=skin_session_key,
            scope_key=f"{MojingTaskType.SUBAGENT_DISPATCH}:{skin_session_key}",
            service_role="mojing:subagent-dispatch",
        )

    async def _current_image_analysis_source(self) -> dict[str, str]:
        task = await self._latest_succeeded_image_analysis_task()
        payload = dict((task or {}).get("payload") or {})
        image_id = str(payload.get("image_id") or "").strip()
        source: dict[str, str] = {}
        task_id = str((task or {}).get("task_id") or "").strip()
        if task_id:
            source["source_task_id"] = task_id
        if image_id:
            source["source_image_id"] = image_id
        image_ref = str(payload.get("image") or payload.get("image_ref") or "").strip()
        if image_ref:
            source["source_image_ref"] = image_ref
        return source

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
            logger.warning("notify_skin_diary_chat image source lookup failed: tenant={} err={}", self._tenant_key, exc)
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
                        "notify_skin_diary_chat image job lookup failed: tenant={} job_id={} err={}",
                        self._tenant_key,
                        job_id,
                        exc,
                    )
                    job = None
                job_time = _task_time(dict(job or {}), "completed_at", "updated_at", "created_at")
                if job_time is not None:
                    return job_time
        return _task_time(task, "completed_at", "updated_at", "created_at")

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
                    "当前没有可用的已完成自拍/肤况分析。不要说肌肤日记已经提交或开始生成。"
                    "请告诉用户：为了生成今日肌肤日记，需要先补一张当前清晰自拍。"
                ),
                model_guidance=(
                    "本次 notify_skin_diary_chat 没有派发，因为没有找到已成功完成的自拍/肤况分析任务。"
                    "请先引导用户上传当前清晰自拍，等图片分析完成后再生成肌肤日记。"
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
                f"用户上次自拍/肤况图是{age}。不要说肌肤日记已经提交或开始生成。"
                "请告诉用户：为了让今日肌肤日记更贴近当前状态，建议先补一张当前清晰自拍；"
                "如果现在不方便，也可以明确说沿用之前那张。"
            ),
            model_guidance=(
                "本次 notify_skin_diary_chat 没有派发，因为当前可用自拍已超过时效。"
                "先让用户选择补新图或沿用旧图；用户明确沿用旧图后，可以再次调用 "
                "notify_skin_diary_chat(intent=\"handoff\", allow_stale_selfie=true)。"
            ),
        )

    async def on_task_submitted(self, task: TaskEnvelope, queue_id: str) -> None:
        logger.info(
            "notify_skin_diary_chat: queued task for tenant={} session_key={} queue_id={}",
            self._tenant_key,
            task.session_key,
            queue_id,
        )

    def durable_result(self, task: TaskEnvelope, queue_id: str) -> ToolResult:
        contract = task.payload.get("handoff_contract") if isinstance(task.payload, dict) else {}
        intent = str((contract or {}).get("intent") or "chat")
        if intent == "handoff":
            message_focus = (
                "已把生成或刷新肌肤日记的请求交给肌肤日记助手。"
                "请告诉用户后续去【肌肤日记】页面查看生成进度或结果。"
                "不要说成主 Agent 已经完成分析，也不要复述具体肌肤结论。"
            )
        else:
            message_focus = (
                "已把这个肌肤日记相关问题交给肌肤日记助手继续看。"
                "请告诉用户去【肌肤日记】页面继续看具体解释或护理安排。"
                "不要说成主 Agent 已经完成分析，也不要复述具体肌肤结论。"
            )
        return tool_submitted(
            tool=self.name,
            task_id=task.task_id,
            queue_id=queue_id,
            subagent="skin_diary_chat",
            where="肌肤日记页面",
            message_focus=message_focus,
        )


def _normalize_intent(value: object) -> str:
    text = str(value or "").strip()
    if text == "handoff":
        return "handoff"
    return "chat"


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
