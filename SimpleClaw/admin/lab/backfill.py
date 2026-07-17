"""/admin/lab 历史照片回填：zip 解析、日期映射纯函数 + 回填编排。

纯函数（extract_photos / backfill_dates / *_timestamp）无任何 IO，便于单测；
BackfillRunner 串行编排真实链路：TOS 上传 → 真实图片分析 → 等画像落库 →
USER.md sync → created_at 改写为历史日期。
"""

from __future__ import annotations

import asyncio
import io
import time
import uuid
import zipfile
from datetime import date, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Awaitable, Callable

from loguru import logger

ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_PHOTOS = 60
MAX_PHOTO_BYTES = 15 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024


def extract_photos(zip_bytes: bytes) -> list[tuple[str, bytes]]:
    """解析 zip，按 basename 字典序返回 (文件名, 字节) 列表。

    跳过目录、__MACOSX、任一路径段以 . 开头的条目、非白名单扩展名；
    张数 / 单文件 / 总解压量超限时抛 ValueError（中文消息，前端可直接展示）。
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except (zipfile.BadZipFile, ValueError) as exc:
        raise ValueError(f"无法解析 zip 文件：{exc}") from exc

    candidates: list[tuple[tuple[str, str], str, zipfile.ZipInfo]] = []
    total_bytes = 0
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            path = PurePosixPath(info.filename)
            parts = path.parts
            if any(part == "__MACOSX" or part.startswith(".") for part in parts):
                continue
            basename = path.name
            if path.suffix.lower() not in ALLOWED_EXTS:
                continue
            if info.file_size > MAX_PHOTO_BYTES:
                raise ValueError(
                    f"照片 {basename} 超过单文件上限 {MAX_PHOTO_BYTES // (1024 * 1024)}MB"
                )
            total_bytes += info.file_size
            if total_bytes > MAX_TOTAL_BYTES:
                raise ValueError(f"zip 解压总量超过 {MAX_TOTAL_BYTES // (1024 * 1024)}MB 上限")
            candidates.append(((basename.lower(), info.filename), basename, info))

        if not candidates:
            raise ValueError("zip 里没有找到照片（支持 jpg/jpeg/png/webp）")
        if len(candidates) > MAX_PHOTOS:
            raise ValueError(f"照片数量 {len(candidates)} 超过上限 {MAX_PHOTOS} 张")

        candidates.sort(key=lambda item: item[0])
        return [(basename, zf.read(info)) for _, basename, info in candidates]


def backfill_dates(n: int, *, today: date | None = None) -> list[date]:
    """返回 n 个连续业务日（升序），最后一个 = today（缺省取当前北京业务日）。"""
    if n <= 0:
        raise ValueError("照片数量必须大于 0")
    if today is None:
        from Mojing.agent.skin_trend import business_date_of
        from Mojing.utils.skin_diary_time import to_beijing_time

        today = business_date_of(to_beijing_time(None))
    return [today - timedelta(days=n - 1 - i) for i in range(n)]


def job_timestamp(day: date) -> datetime:
    """nb_image_analysis_jobs 用的历史时刻：UTC naive（与 repo 的 utcnow 约定一致）。

    取 UTC 04:00 == 北京 12:00，与 profile_timestamp 是同一瞬间。
    """
    return datetime(day.year, day.month, day.day, 4, 0, 0)


def profile_timestamp(day: date) -> datetime:
    """nb_tenant_skin_profiles 用的历史时刻：北京 naive（business_date_of 把 naive 当北京时间）。

    取北京 12:00，远离 business_date_of 的凌晨 4 点业务日边界。
    """
    return datetime(day.year, day.month, day.day, 12, 0, 0)


# ---------------------------------------------------------------------------
# 回填编排
# ---------------------------------------------------------------------------

# 内存 job 注册表：lab 是测试工具，重启丢进度可接受（DB 里已落的数据不丢）。
BACKFILL_JOBS: dict[str, dict[str, Any]] = {}


def new_job_state(
    *,
    user_id: str,
    photos: list[tuple[str, bytes]],
    days: list[date],
) -> dict[str, Any]:
    """创建并注册一个回填 job 的进度状态对象。"""
    state = {
        "job_id": uuid.uuid4().hex,
        "user_id": user_id,
        "state": "running",
        "items": [
            {
                "filename": filename,
                "target_date": day.isoformat(),
                "stage": "pending",
                "image_url": "",
                "profile_id": None,
                "error": "",
            }
            for (filename, _), day in zip(photos, days)
        ],
    }
    BACKFILL_JOBS[state["job_id"]] = state
    return state


class BackfillRunner:
    """串行处理每张照片：TOS 上传 → 真实分析 → 等画像 → USER.md sync → 改写历史日期。

    绕过 runtime 任务队列直接调用 analysis executor 函数：payload/job 状态机
    与真实链路完全一致，但不产生 wait_external runtime task——避免
    WaitExternalTaskMonitor 与"改写 created_at"竞态（匹配不到 → 超时 →
    failure activation 灌进聊天会话）。sync 仍走真实 SKIN_PROFILE_SYNC 任务。
    """

    def __init__(
        self,
        *,
        uploader: Any,
        image_repo: Any,
        skin_profile_repo: Any,
        runtime: Any,
        analysis_execute: Callable[[Any], Awaitable[Any]],
        profile_timeout_s: float = 120.0,
        sync_timeout_s: float = 60.0,
        poll_interval_s: float = 3.0,
    ) -> None:
        self._uploader = uploader
        self._image_repo = image_repo
        self._skin_profile_repo = skin_profile_repo
        self._runtime = runtime
        self._analysis_execute = analysis_execute
        self._profile_timeout_s = profile_timeout_s
        self._sync_timeout_s = sync_timeout_s
        self._poll_interval_s = poll_interval_s

    async def run(
        self,
        state: dict[str, Any],
        *,
        user_id: str,
        photos: list[tuple[str, bytes]],
        days: list[date],
    ) -> None:
        try:
            for item, (filename, data), day in zip(state["items"], photos, days):
                try:
                    await self._run_one(item, user_id=user_id, filename=filename, data=data, day=day)
                except Exception as exc:
                    logger.warning("lab.backfill item failed: file={} err={}", filename, exc)
                    item["stage"] = "failed"
                    item["error"] = str(exc)
            state["state"] = "done"
        except Exception as exc:  # 批级意外错误（不应发生，逐张错误已在循环内吞掉）
            logger.exception("lab.backfill batch failed: {}", exc)
            state["state"] = "failed"
            state["error"] = str(exc)

    async def _run_one(
        self,
        item: dict[str, Any],
        *,
        user_id: str,
        filename: str,
        data: bytes,
        day: date,
    ) -> None:
        from Mojing.tools.image_tools import build_image_analysis_envelope
        from Mojing.storage.image_repo import normalize_image_ref
        from admin.lab.tos_uploader import make_photo_key

        session_key = f"main:{user_id}"

        item["stage"] = "uploading_tos"
        key = make_photo_key(user_id=user_id, day=day.strftime("%Y%m%d"), filename=filename)
        image_url = normalize_image_ref(await self._uploader.upload(key=key, data=data))
        item["image_url"] = image_url

        item["stage"] = "analyzing"
        job = await self._image_repo.create_job(
            tenant_key=user_id,
            session_key=session_key,
            image_ref=image_url,
            status="uploaded",
        )
        job_id = str(job["job_id"])
        envelope = build_image_analysis_envelope(
            tenant_key=user_id,
            session_key=session_key,
            image_ref=image_url,
            job_id=job_id,
            image_id=str(job.get("image_id") or ""),
            source="explicit",
        )
        submitted_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        result = await self._analysis_execute(envelope)
        if getattr(result, "status", "") == "failed":
            error = str(result.error or result.summary or "unknown")
            # 真实 executor 已 mark_failed，这里兜底重写一次（幂等）
            await self._image_repo.mark_failed(job_id, error=f"lab backfill: {error}")
            raise RuntimeError(f"图片分析派发失败：{error}")

        item["stage"] = "waiting_profile"
        message_id = str(envelope.payload.get("message_id") or job_id)
        profile = await self._poll_profile(
            user_id,
            since=submitted_at,
            message_id=message_id,
            image_ref=image_url,
            timeout_s=self._profile_timeout_s,
        )
        if profile is None:
            await self._image_repo.mark_failed(job_id, error="lab backfill: 等待画像超时")
            raise RuntimeError(f"等待画像落库超时（{int(self._profile_timeout_s)}s），外部分析服务未返回")
        profile_id = profile.get("profile_id")
        item["profile_id"] = profile_id
        await self._image_repo.mark_succeeded(job_id, profile_id=profile_id, summary="lab backfill")

        item["stage"] = "syncing_profile"
        synced = await self._sync_profile(
            user_id,
            session_key=session_key,
            profile=profile,
            since=submitted_at,
            message_id=message_id,
        )
        if not synced:
            # sync 超时不算整张失败：画像数据已在库，趋势链路可用，仅 USER.md 可能滞后
            item["error"] = "USER.md 同步超时（画像已落库，仅文档可能滞后）"

        item["stage"] = "backdating"
        await self._skin_profile_repo.backdate_profile(profile_id, created_at=profile_timestamp(day))
        await self._image_repo.backdate_job(job_id, created_at=job_timestamp(day))

        item["stage"] = "done"

    async def _poll_profile(
        self,
        user_id: str,
        *,
        since: str,
        message_id: str,
        image_ref: str,
        timeout_s: float,
    ) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            profile = await self._skin_profile_repo.find_profile_since(
                tenant_key=user_id,
                since=since,
                message_id=message_id,
                image_ref=image_ref,
            )
            if profile is not None:
                return profile
            await asyncio.sleep(self._poll_interval_s)
        return None

    async def _sync_profile(
        self,
        user_id: str,
        *,
        session_key: str,
        profile: dict[str, Any],
        since: str,
        message_id: str,
    ) -> bool:
        """与 WaitExternalTaskMonitor._enqueue_skin_profile_sync 同构地入队真实 sync 任务。"""
        from simpleclaw.runtime.task_protocol import TaskEnvelope
        from Mojing.runtime.streams import MojingTaskStream
        from Mojing.runtime.task_types import MojingTaskType

        sync_status = str(profile.get("sync_status") or "").strip().lower()
        if sync_status and sync_status != "pending":
            return True

        sync_task = TaskEnvelope(
            task_type=MojingTaskType.SKIN_PROFILE_SYNC,
            payload={
                "tenant_key": user_id,
                "session_key": session_key,
                "profile_id": profile.get("profile_id"),
                "source": "lab_backfill",
            },
            stream=MojingTaskStream.POSTPROCESS,
            tenant_key=user_id,
            session_key=session_key,
            scope_key=f"postprocess:{user_id}:USER.md",
            service_role="mojing:skin-profile-sync:lab",
        )
        await self._runtime.submit_task(sync_task, summary="lab backfill: sync profile to USER.md")

        deadline = time.monotonic() + self._sync_timeout_s
        while time.monotonic() < deadline:
            fresh = await self._skin_profile_repo.find_profile_since(
                tenant_key=user_id,
                since=since,
                message_id=message_id,
            )
            status = str((fresh or {}).get("sync_status") or "").strip().lower()
            if status in {"synced", "skipped"}:
                return True
            await asyncio.sleep(self._poll_interval_s)
        return False
