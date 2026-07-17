"""图片分析工具 — 将图片分析任务派发为 durable task。

AnalyzeImageTool 是耦合 durable 工具（needs_followup=True）：
LLM 先判断图片是否适合分析并触发工具；工具声明 durable task，
ToolExecutionRuntime 统一入队后，把提交成功的 ack 回灌给 LLM，
由 LLM 用自然语言告知用户“图片分析已派发，反馈回来会告诉你”。

图片解析优先级：
  1. 本轮上传的图片（media 中的最后一张）
  2. 最近一次存库的图片（ImageRepository.get_latest）

去重：同一张图在 dedupe_window_s 秒内不重复投递。

build_image_analysis_envelope（模块级辅助）：
  LLM 主动调用 analyze_image 后使用这份 envelope 构造逻辑，
  保证 payload 字段一致。上传路径只记录图片资产，不自动派发分析。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.tools.base import Tool, ToolResult
from Mojing.runtime.streams import MojingTaskStream
from Mojing.runtime.task_types import MojingTaskType
from Mojing.runtime.tool_results import tool_deduped, tool_submitted
from Mojing.storage.image_repo import ImageRepository, normalize_image_ref


def build_image_analysis_envelope(
    *,
    tenant_key: str,
    session_key: str,
    origin_session_key: str | None = None,
    image_ref: str,
    job_id: str | None = None,
    image_id: str | None = None,
    message_id: str | None = None,
    query: str = "",
    source: str = "current_turn",
) -> TaskEnvelope:
    """构造图片分析任务信封；上传路径和 LLM 主动调用共用。

    Args:
        source: "current_turn"（LLM 用本轮上传图）
                / "latest_known"（LLM 用历史最近一张）/ "explicit"
    """
    image_ref = normalize_image_ref(image_ref)
    job_id = str(job_id or uuid.uuid4().hex)
    msg_id = message_id or job_id
    image_id = str(image_id or hashlib.md5(image_ref.encode()).hexdigest())
    payload_session_id = str(origin_session_key or "").strip() or session_key

    payload = {
        "job_id": job_id,
        "tenant_key": tenant_key,
        "session_key": session_key,
        "origin_session_key": payload_session_id,
        "session_id": payload_session_id,
        "message_id": msg_id,
        "image_id": image_id,
        "user_id": tenant_key,
        "query": query,
        "image": image_ref,
        "agent_state": "image_full",
        "source": source,
    }

    return TaskEnvelope(
        task_type=MojingTaskType.IMAGE_ANALYSIS,
        payload=payload,
        stream=MojingTaskStream.IMAGE_ANALYSIS,
        tenant_key=tenant_key,
        session_key=session_key,
        scope_key=f"image_analysis:{tenant_key}:{image_id}",
        service_role="mojing:image-analysis",
    )


class AnalyzeImageTool(Tool):
    """Trigger image analysis after the main agent judges the photo is suitable."""

    name = "analyze_image"
    description = (
        "Trigger image analysis after you judge the user's uploaded photo is clear, "
        "likely a face/selfie, and relevant to skin analysis."
    )
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    needs_followup = True
    execution_mode = "durable"
    durable_action = "submitted"
    tool_category = "async_task"
    business_ref_type = "image_analysis_job"
    business_ref_id_field = "job_id"

    def __init__(
        self,
        *,
        image_repo: ImageRepository,
        runtime_task_repo=None,
        dedupe_window_s: int = 300,
    ) -> None:
        self._image_repo = image_repo
        self._runtime_task_repo = runtime_task_repo
        self._dedupe_window_s = max(0, dedupe_window_s)
        # 由 set_context() 每轮更新
        self._tenant_key = "__default__"
        self._session_key = "cli:direct"
        self._origin_session_key = ""
        self._query = ""
        self._media: list[str] = []
        self._message_id: str | None = None
        self._has_succeeded_image_analysis: bool = False

    def set_context(
        self,
        *,
        tenant_key: str = "",
        session_key: str = "",
        origin_session_key: str = "",
        query: str = "",
        media: list[str] | None = None,
        message_id: str | None = None,
        **_,
    ) -> None:
        """由 SessionStore.set_turn_context() 在每轮开始时调用。"""
        if tenant_key:
            self._tenant_key = tenant_key
        if session_key:
            self._session_key = session_key
        self._origin_session_key = str(origin_session_key or "").strip()
        self._query = query
        self._media = [normalize_image_ref(ref) for ref in list(media or []) if normalize_image_ref(ref)]
        self._message_id = message_id
        self._has_succeeded_image_analysis = False

    async def prepare_task(self, **_) -> TaskEnvelope | ToolResult:
        # 解析图片：本轮上传 → 最近存库
        image_ref = ""
        source = "explicit"
        if self._media:
            image_ref = normalize_image_ref(self._media[-1])
            source = "current_turn"
        if not image_ref:
            image_ref = await self._image_repo.get_latest(self._tenant_key) or ""
            source = "latest_known"
        if not image_ref:
            logger.warning("analyze_image: no image available for tenant={}", self._tenant_key)
            return ToolResult(content=json.dumps({"ok": False, "error": "no image available"}), ok=False)

        deduped = await self._dedupe_existing_analysis(image_ref)
        if deduped is not None:
            return deduped

        job = await self._find_reusable_uploaded_job(image_ref)
        if job is None:
            try:
                job = await self._image_repo.create_job(
                    tenant_key=self._tenant_key,
                    session_key=self._session_key,
                    image_ref=image_ref,
                    message_id=self._message_id,
                    status="uploaded",
                )
            except Exception as exc:
                logger.warning("analyze_image create_job failed: tenant={} err={}", self._tenant_key, exc)
                return ToolResult(
                    content=json.dumps({"ok": False, "error": "failed to create image analysis job"}),
                    ok=False,
                )

        await self._load_image_analysis_history()
        return build_image_analysis_envelope(
            tenant_key=self._tenant_key,
            session_key=self._session_key,
            origin_session_key=self._origin_session_key,
            image_ref=image_ref,
            job_id=str(job["job_id"]),
            image_id=str(job["image_id"]),
            message_id=self._message_id,
            query=self._query,
            source=source,
        )

    async def _load_image_analysis_history(self) -> None:
        self._has_succeeded_image_analysis = False
        if self._runtime_task_repo is None or not hasattr(self._runtime_task_repo, "has_succeeded_task_for"):
            return
        try:
            self._has_succeeded_image_analysis = await self._runtime_task_repo.has_succeeded_task_for(
                tenant_key=self._tenant_key,
                task_type=MojingTaskType.IMAGE_ANALYSIS,
            )
        except Exception as exc:
            logger.warning("analyze_image history lookup failed: tenant={} err={}", self._tenant_key, exc)

    async def on_task_submitted(self, task: TaskEnvelope, queue_id: str) -> None:
        try:
            await self._image_repo.mark_queued(
                str(task.payload.get("job_id") or ""),
                task_id=task.task_id,
                queue_id=queue_id,
                payload=task.payload,
            )
        except Exception as exc:
            logger.warning(
                "analyze_image mark_queued failed: tenant={} job_id={} err={}",
                self._tenant_key,
                task.payload.get("job_id"),
                exc,
            )
        logger.info(
            "analyze_image queued: tenant={} session_key={} message_id={} job_id={} queue_id={} image={}",
            self._tenant_key,
            self._session_key,
            self._message_id or "",
            task.payload.get("job_id"),
            queue_id,
            task.payload.get("image"),
        )

    async def _dedupe_existing_analysis(self, image_ref: str) -> ToolResult | None:
        if self._dedupe_window_s <= 0:
            return None
        image_job_result, allow_runtime_fallback = await self._dedupe_by_image_job(image_ref)
        if image_job_result is not None:
            return image_job_result
        if not allow_runtime_fallback:
            return None
        return await self._dedupe_by_runtime_task()

    async def _dedupe_by_image_job(self, image_ref: str) -> tuple[ToolResult | None, bool]:
        """Return (dedupe_result, allow_runtime_fallback)."""
        if not hasattr(self._image_repo, "find_latest_job"):
            return None, True
        try:
            latest = await self._image_repo.find_latest_job(self._tenant_key)
        except Exception as exc:
            logger.warning("analyze_image image job dedupe lookup failed: {}", exc)
            return None, True
        if not latest:
            return None, True
        latest_ref = str(latest.get("image_ref") or "").strip()
        if latest_ref != str(image_ref or "").strip():
            return None, False
        status = str(latest.get("status") or "").strip().lower()
        if status in {"uploaded", "stored", "failed"}:
            return None, False
        age_s = _age_seconds(latest.get("created_at") or latest.get("updated_at"))
        if age_s is None or age_s >= self._dedupe_window_s:
            return None, False
        return (
            _deduped_result(
                source="image_job",
                status=status or "unknown",
                job_id=str(latest.get("job_id") or ""),
                age_s=age_s,
            ),
            False,
        )

    async def _find_reusable_uploaded_job(self, image_ref: str) -> dict[str, Any] | None:
        """Reuse the current uploaded asset row instead of creating a duplicate job."""
        if not hasattr(self._image_repo, "find_latest_job"):
            return None
        try:
            latest = await self._image_repo.find_latest_job(self._tenant_key)
        except Exception as exc:
            logger.warning("analyze_image uploaded job lookup failed: {}", exc)
            return None
        if not latest:
            return None
        if str(latest.get("image_ref") or "").strip() != str(image_ref or "").strip():
            return None
        status = str(latest.get("status") or "").strip().lower()
        if status not in {"uploaded", "stored"}:
            return None
        return latest

    async def _dedupe_by_runtime_task(self) -> ToolResult | None:
        if self._runtime_task_repo is None or not hasattr(self._runtime_task_repo, "find_latest_task_for"):
            return None
        try:
            latest = await self._runtime_task_repo.find_latest_task_for(
                tenant_key=self._tenant_key,
                task_type=MojingTaskType.IMAGE_ANALYSIS,
            )
        except Exception as exc:
            logger.warning("analyze_image runtime dedupe lookup failed: {}", exc)
            return None
        if not latest:
            return None
        status = str(latest.get("status") or "").strip().lower()
        if status == "failed":
            return None
        age_s = _age_seconds(latest.get("created_at") or latest.get("updated_at"))
        if age_s is None or age_s >= self._dedupe_window_s:
            return None
        return _deduped_result(
            source="runtime_task",
            status=status or "unknown",
            task_id=str(latest.get("task_id") or ""),
            age_s=age_s,
        )

    def durable_result(self, task: TaskEnvelope, queue_id: str) -> ToolResult:
        """触发型工具的标准 ack。

        这里只表示图片分析任务派发成功，不表示图片分析已经完成。
        LLM 会在下一轮基于 message_focus 做一句自然确认。
        """
        return tool_submitted(
            tool=self.name,
            task_id=task.task_id,
            queue_id=queue_id,
            job_id=task.payload.get("job_id"),
            business_ref_type=self.business_ref_type,
            business_ref_id=task.payload.get("job_id"),
            message_focus=self._submitted_message_focus(),
            model_guidance=(
                "只表示图片分析已提交；不要说分析或肌肤日记已经完成。"
            ),
        )

    def _submitted_message_focus(self) -> str:
        if not self._has_succeeded_image_analysis:
            selfie_context = "first_selfie"
            plan_policy = (
                "首次自拍可告知用户：等图片分析完成并同步肤况后，会继续给她同步生成今天的护肤建议；"
                "若用户已明确要肌肤日记，别再反问。"
            )
        else:
            selfie_context = "repeat_selfie"
            plan_policy = (
                "重复自拍默认不承诺更新肌肤日记；"
                "若用户已明确要求，就确认结果回来后接着生成或更新。"
            )
        return (
            f"图片分析已提交，selfie_context={selfie_context}。"
            "自然确认已提交，反馈回来会告诉用户。"
            f"{plan_policy}"
        )


def _age_seconds(value: Any) -> float | None:
    dt = _coerce_datetime(value)
    if dt is None:
        return None
    now = datetime.now(dt.tzinfo) if dt.tzinfo is not None else datetime.now(UTC).replace(tzinfo=None)
    return max(0.0, (now - dt).total_seconds())


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
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _deduped_result(
    *,
    source: str,
    status: str,
    age_s: float,
    job_id: str = "",
    task_id: str = "",
) -> ToolResult:
    extra: dict[str, Any] = {
        "business_status": status,
        "age_seconds": int(age_s),
    }
    if job_id:
        extra["job_id"] = job_id
        extra["business_ref_type"] = "image_analysis_job"
        extra["business_ref_id"] = job_id
    if task_id:
        extra["task_id"] = task_id
    return tool_deduped(
        reason="recent_image_analysis_exists",
        phase="in_progress",
        source=source,
        runtime_task_status=status if source == "runtime_task" else None,
        message_focus=(
            "这张图片最近已经提交过基础图片分析，不要重复触发 analyze_image。"
            "如果用户追问专业分析结果，先正常聊天或说明基础分析仍在处理中。"
        ),
        **extra,
    )
