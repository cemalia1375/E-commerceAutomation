"""ImageRepository — 存储并检索租户上传的图片引用。

nb_image_analysis_jobs 表用于持久化每次上传的图片 URL，
ContextBuilder 通过 get_latest() 在后续对话轮次中复用最近一张图片，
使主 Agent 在用户没有重新上传图片时也能"看到"皮肤照片。

表关键字段（NOT NULL）：
  job_id (PK), tenant_key, session_key, image_id, image_ref,
  focus, status, created_at
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from Mojing.storage.database import Database


class ImageRepository:
    """对 nb_image_analysis_jobs 的轻量读写封装层。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def store(
        self,
        tenant_key: str,
        session_key: str,
        image_ref: str,
    ) -> None:
        """存储一条图片记录（每次上传时调用）。"""
        await self.create_job(
            tenant_key=tenant_key,
            session_key=session_key,
            image_ref=image_ref,
        )

    async def create_job(
        self,
        *,
        tenant_key: str,
        session_key: str,
        image_ref: str,
        message_id: str | None = None,
        focus: str = "image_full",
        status: str = "uploaded",
    ) -> dict[str, Any]:
        """创建一条图片分析业务 job，并返回可传入 runtime payload 的关键字段。"""
        image_ref = normalize_image_ref(image_ref)
        now = _now()
        job_id = uuid.uuid4().hex
        image_id = _image_id(image_ref)

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nb_image_analysis_jobs
                        (job_id, tenant_key, session_key, image_id, image_ref,
                         focus, status, message_id, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        job_id,
                        tenant_key,
                        session_key,
                        image_id,
                        image_ref,
                        focus,
                        status,
                        message_id,
                        now,
                        now,
                    ),
                )
        return {
            "job_id": job_id,
            "tenant_key": tenant_key,
            "session_key": session_key,
            "message_id": message_id,
            "image_id": image_id,
            "image_ref": image_ref,
            "focus": focus,
            "status": status,
            "created_at": now,
            "updated_at": now,
        }

    async def get_latest(self, tenant_key: str) -> str | None:
        """返回该租户最近一次上传的图片 URL，若无则返回 None。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT image_ref
                    FROM nb_image_analysis_jobs
                    WHERE tenant_key = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (tenant_key,),
                )
                row = await cur.fetchone()
        return normalize_image_ref(row[0]) if row else None

    async def get_latest_excluding(self, tenant_key: str, exclude_refs: list[str] | None = None) -> str | None:
        """返回排除指定图片后的最近上传图片 URL，用于重拍时取原图。"""
        row = await self.get_latest_record_excluding(tenant_key, exclude_refs)
        return str(row["image_ref"]) if row else None

    async def get_latest_succeeded_record_excluding(
        self,
        tenant_key: str,
        exclude_refs: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """返回最近一张已完成图片分析的图片记录。

        历史皮肤照召回只能使用已完成分析的图片，避免把护肤品图等普通上传资产
        当成可用于肤况判断的历史自拍。
        """
        return await self._get_latest_record_excluding(
            tenant_key,
            exclude_refs=exclude_refs,
            statuses=("succeeded",),
        )

    async def get_latest_record_excluding(
        self,
        tenant_key: str,
        exclude_refs: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """返回最近图片记录，可排除本轮用户刚上传的图片。"""
        return await self._get_latest_record_excluding(tenant_key, exclude_refs=exclude_refs)

    async def _get_latest_record_excluding(
        self,
        tenant_key: str,
        *,
        exclude_refs: list[str] | None = None,
        statuses: tuple[str, ...] = (),
    ) -> dict[str, Any] | None:
        excluded = {
            normalize_image_ref(ref)
            for ref in (exclude_refs or [])
            if normalize_image_ref(ref)
        }
        clean_statuses = tuple(str(status or "").strip() for status in statuses if str(status or "").strip())

        where = "WHERE tenant_key = %s"
        params: list[Any] = [tenant_key]
        if clean_statuses:
            placeholders = ", ".join(["%s"] * len(clean_statuses))
            where += f" AND status IN ({placeholders})"
            params.extend(clean_statuses)
        limit = 20 if excluded else 1

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""\
                    SELECT job_id, tenant_key, session_key, image_id, image_ref, status,
                           request_payload_json, created_at, updated_at, last_error
                    FROM nb_image_analysis_jobs
                    {where}
                    ORDER BY created_at DESC
                    LIMIT {limit}
                    """,
                    tuple(params),
                )
                rows = await cur.fetchall()
        for row in rows:
            image_ref = normalize_image_ref(row[4])
            if excluded and image_ref in excluded:
                continue
            return _image_job_record(row, image_ref=image_ref)
        return None

    async def find_latest_job(self, tenant_key: str) -> dict[str, Any] | None:
        """返回该租户最近一条图片分析 job。"""
        return await self.get_latest_record_excluding(tenant_key)

    async def get_job_by_id(self, tenant_key: str, job_id: str) -> dict[str, Any] | None:
        """按 job_id 返回该租户的一条图片分析 job。"""
        job_id = str(job_id or "").strip()
        if not job_id:
            return None
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT job_id, tenant_key, session_key, image_id, image_ref, status,
                           request_payload_json, created_at, updated_at, last_error,
                           completed_at
                    FROM nb_image_analysis_jobs
                    WHERE tenant_key = %s AND job_id = %s
                    LIMIT 1
                    """,
                    (tenant_key, job_id),
                )
                row = await cur.fetchone()
        if row is None:
            return None
        return {
            "job_id": row[0],
            "tenant_key": row[1],
            "session_key": row[2],
            "image_id": row[3],
            "image_ref": normalize_image_ref(row[4]),
            "status": row[5],
            "request_payload": _parse_json(row[6]),
            "created_at": _parse_datetime(row[7]),
            "updated_at": _parse_datetime(row[8]),
            "last_error": row[9],
            "completed_at": _parse_datetime(row[10]) if row[10] else None,
        }

    async def get_latest_time(self, tenant_key: str) -> "datetime | None":
        """返回该租户最近一次上传图片的时间（UTC），若无则返回 None。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT created_at
                    FROM nb_image_analysis_jobs
                    WHERE tenant_key = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (tenant_key,),
                )
                row = await cur.fetchone()
        if row is None:
            return None
        val = row[0]
        return _parse_datetime(val)

    async def mark_queued(
        self,
        job_id: str,
        *,
        task_id: str,
        queue_id: str,
        payload: dict[str, Any],
    ) -> None:
        await self._update_job(
            job_id,
            status="queued",
            request_payload={
                "runtime_task_id": task_id,
                "queue_id": queue_id,
                "payload": payload,
            },
        )

    async def mark_running(self, job_id: str) -> None:
        await self._update_job(job_id, status="running", started_at=_now())

    async def mark_wait_external(
        self,
        job_id: str,
        *,
        external_job_id: str | None = None,
        response: Any = None,
    ) -> None:
        await self._update_job(
            job_id,
            status="wait_external",
            external_job_id=external_job_id,
            result_update={"trigger_response": _json_safe(response)},
        )

    async def mark_triggered(
        self,
        job_id: str,
        *,
        external_job_id: str | None = None,
        response: Any = None,
    ) -> None:
        """兼容旧调用名，实际写入 wait_external。"""
        await self.mark_wait_external(
            job_id,
            external_job_id=external_job_id,
            response=response,
        )

    async def mark_profile_available(
        self,
        job_id: str,
        *,
        profile_id: int | str | None = None,
        summary: str | None = None,
    ) -> None:
        """兼容旧调用名：图片画像结果已落库，即图片分析任务成功。"""
        await self.mark_succeeded(
            job_id,
            profile_id=profile_id,
            summary=summary,
        )

    async def mark_succeeded(
        self,
        job_id: str,
        *,
        profile_id: int | str | None = None,
        summary: str | None = None,
    ) -> None:
        await self._update_job(
            job_id,
            status="succeeded",
            completed_at=_now(),
            summary_text=summary,
            result_update={"profile_id": profile_id},
        )

    async def mark_user_md_synced(
        self,
        job_id: str,
        *,
        profile_id: int | str | None = None,
        sync_outcome: str,
    ) -> None:
        """兼容旧调用名；USER.md 同步不再写 image job。"""
        del job_id, profile_id, sync_outcome

    async def mark_failed(self, job_id: str, *, error: str) -> None:
        await self._update_job(
            job_id,
            status="failed",
            last_error=str(error or "")[:2000],
            completed_at=_now(),
        )

    async def mark_profile_available_for_profile(
        self,
        tenant_key: str,
        profile: dict[str, Any],
        *,
        summary: str | None = None,
    ) -> None:
        """兼容旧调用名：按 profile 反查 image job 并标记图片分析成功。"""
        await self.mark_succeeded_for_profile(
            tenant_key,
            profile,
            summary=summary,
        )

    async def mark_succeeded_for_profile(
        self,
        tenant_key: str,
        profile: dict[str, Any],
        *,
        summary: str | None = None,
    ) -> None:
        job = await self.find_job_for_profile(tenant_key, profile)
        if job:
            await self.mark_succeeded(
                str(job["job_id"]),
                profile_id=profile.get("profile_id"),
                summary=summary,
            )

    async def mark_user_md_synced_for_profile(
        self,
        tenant_key: str,
        profile: dict[str, Any],
        *,
        sync_outcome: str,
    ) -> None:
        """兼容旧调用名；USER.md 同步不再写 image job。"""
        del tenant_key, profile, sync_outcome

    async def mark_failed_for_profile(
        self,
        tenant_key: str,
        profile: dict[str, Any],
        *,
        error: str,
    ) -> None:
        job = await self.find_job_for_profile(tenant_key, profile)
        if job:
            await self.mark_failed(str(job["job_id"]), error=error)

    async def find_job_for_profile(
        self,
        tenant_key: str,
        profile: dict[str, Any],
    ) -> dict[str, Any] | None:
        """按 profile 可用字段反查最可能对应的 image job。"""
        candidates = _profile_job_lookup_candidates(profile)
        if not candidates:
            return None

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                for clause, value in candidates:
                    await cur.execute(
                        f"""
                        SELECT job_id, tenant_key, session_key, image_id, image_ref, status,
                               request_payload_json, created_at, updated_at, last_error
                        FROM nb_image_analysis_jobs
                        WHERE tenant_key = %s AND {clause}
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        (tenant_key, value),
                    )
                    row = await cur.fetchone()
                    if row is not None:
                        return {
                            "job_id": row[0],
                            "tenant_key": row[1],
                            "session_key": row[2],
                            "image_id": row[3],
                            "image_ref": normalize_image_ref(row[4]),
                            "status": row[5],
                            "request_payload": _parse_json(row[6]),
                            "created_at": _parse_datetime(row[7]),
                            "updated_at": _parse_datetime(row[8]),
                            "last_error": row[9],
                        }
        return None

    async def backdate_job(self, job_id: str, *, created_at: "datetime") -> None:
        """测试回填专用：把 job 的全部时间列整体改写为历史时刻。

        /admin/lab 历史照片回填在分析完成后调用，让 probe/snapshot 视图
        与 SelfieAgeAttentionProvider 读到的时间一致地落在目标历史日。
        """
        ts = created_at.strftime("%Y-%m-%d %H:%M:%S")
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_image_analysis_jobs
                    SET created_at=%s, updated_at=%s, started_at=%s, completed_at=%s
                    WHERE job_id=%s
                    """,
                    (ts, ts, ts, ts, job_id),
                )

    async def _update_job(
        self,
        job_id: str,
        *,
        status: str,
        request_payload: dict[str, Any] | None = None,
        result_update: dict[str, Any] | None = None,
        summary_text: str | None = None,
        external_job_id: str | None = None,
        last_error: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> None:
        now = _now()
        result_json = await self._merged_result_json(job_id, result_update)
        assignments = ["status=%s", "updated_at=%s"]
        params: list[Any] = [status, now]

        if request_payload is not None:
            assignments.append("request_payload_json=%s")
            params.append(json.dumps(request_payload, ensure_ascii=False))
        if result_json is not None:
            assignments.append("result_json=%s")
            params.append(json.dumps(result_json, ensure_ascii=False))
        if summary_text is not None:
            assignments.append("summary_text=%s")
            params.append(summary_text)
        if external_job_id is not None:
            assignments.append("external_job_id=%s")
            params.append(external_job_id)
        if last_error is not None:
            assignments.append("last_error=%s")
            params.append(last_error)
        elif status != "failed":
            assignments.append("last_error=NULL")
        if started_at is not None:
            assignments.append("started_at=%s")
            params.append(started_at)
        if completed_at is not None:
            assignments.append("completed_at=%s")
            params.append(completed_at)

        params.append(job_id)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    UPDATE nb_image_analysis_jobs
                    SET {', '.join(assignments)}
                    WHERE job_id=%s
                    """,
                    tuple(params),
                )

    async def _merged_result_json(
        self,
        job_id: str,
        update: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if update is None:
            return None
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT result_json FROM nb_image_analysis_jobs WHERE job_id=%s LIMIT 1",
                    (job_id,),
                )
                row = await cur.fetchone()
        current = _parse_json(row[0]) if row else {}
        current.update({k: v for k, v in update.items() if v is not None})
        return current


def _parse_datetime(value: Any) -> datetime:
    if hasattr(value, "year"):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def normalize_image_ref(image_ref: Any) -> str:
    ref = str(image_ref or "").strip()
    if not ref:
        return ""
    if (".volces.com/" in ref or ".ivolces.com/" in ref) and "/photos/" in ref and "?" in ref:
        return ref.split("?", 1)[0]
    return ref


def _image_job_record(row: Any, *, image_ref: str | None = None) -> dict[str, Any]:
    return {
        "job_id": row[0],
        "tenant_key": row[1],
        "session_key": row[2],
        "image_id": row[3],
        "image_ref": normalize_image_ref(image_ref if image_ref is not None else row[4]),
        "status": row[5],
        "request_payload": _parse_json(row[6]),
        "created_at": _parse_datetime(row[7]),
        "updated_at": _parse_datetime(row[8]),
        "last_error": row[9],
    }


def _profile_job_lookup_candidates(profile: dict[str, Any]) -> list[tuple[str, str]]:
    """Return ordered WHERE candidates for mapping a skin profile back to an image job."""
    message_id = str(profile.get("message_id") or "").strip()
    image_ref = normalize_image_ref(profile.get("image_url"))
    analysis_id = str(profile.get("analysis_id") or "").strip()

    candidates: list[tuple[str, str]] = []
    if message_id:
        candidates.append(("message_id = %s", message_id))
        candidates.append(("job_id = %s", message_id))
    if image_ref:
        candidates.append(("image_ref = %s", image_ref))
    if analysis_id:
        candidates.append(("image_id = %s", analysis_id))
        if analysis_id.startswith("skin_") and len(analysis_id) > len("skin_"):
            candidates.append(("job_id = %s", analysis_id[len("skin_") :]))

    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _image_id(image_ref: str) -> str:
    return hashlib.md5(image_ref.encode("utf-8")).hexdigest()


def _parse_json(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)
