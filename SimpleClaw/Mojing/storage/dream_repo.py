"""MySQL-backed DreamStore using subagent runtime governance tables."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from simpleclaw.dream.protocol import (
    DreamArtifact,
    DreamCandidate,
    DreamJob,
    DreamStatus,
    now_ms,
)
from simpleclaw.dream.store import DreamStore
from Mojing.storage.database import Database


class DreamRepository(DreamStore):
    """Persist dream candidates, jobs, and artifacts in subagent runtime tables."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def save_candidate(self, candidate: DreamCandidate) -> None:
        metadata = {
            "kind": "dream_candidate",
            "scope_key": candidate.scope_key,
            "dedupe_key": candidate.dedupe_key,
            "trigger": candidate.trigger,
            "namespace": candidate.namespace,
            "source_id": candidate.source_id,
            "input_cursor": candidate.input_cursor,
        }
        await self._upsert_run(
            run_id=candidate.candidate_id,
            tenant_key=candidate.tenant_key,
            session_key=candidate.session_key or "",
            status="candidate",
            owner_type=_owner_type(candidate.source_id),
            owner_id=candidate.source_id,
            runtime_task_id=None,
            trace_id=None,
            objective=candidate.reason,
            input_refs=_input_refs(candidate.source_id, candidate.input_cursor),
            payload=candidate.payload,
            metadata=metadata,
            created_at_ms=candidate.created_at_ms,
        )

    async def update_candidate_status(
        self,
        candidate_id: str,
        status: DreamStatus,
        *,
        updated_at_ms: int | None = None,
    ) -> None:
        await self._update_run_status(
            candidate_id,
            _run_status(status),
            updated_at_ms=updated_at_ms,
        )

    async def save_job(self, job: DreamJob) -> None:
        metadata = {
            "kind": "dream_job",
            "candidate_id": job.candidate_id,
            "scope_key": job.scope_key,
            "trigger": job.trigger,
            "namespace": job.namespace,
            "source_id": job.source_id,
            "input_cursor": job.input_cursor,
        }
        await self._upsert_run(
            run_id=job.job_id,
            tenant_key=job.tenant_key,
            session_key=job.session_key or "",
            status=_run_status(job.status),
            owner_type=_owner_type(job.source_id),
            owner_id=job.source_id or job.candidate_id,
            runtime_task_id=job.job_id,
            trace_id=job.trace_id,
            objective=job.reason,
            input_refs=_input_refs(job.source_id, job.input_cursor),
            payload=job.payload,
            metadata=metadata,
            created_at_ms=job.admitted_at_ms,
        )

    async def get_job(self, job_id: str) -> DreamJob | None:
        row = await self._get_run(job_id)
        if row is None:
            return None
        metadata = _decode_json(row.get("metadata_json")) or {}
        payload = _decode_json(row.get("payload_json")) or {}
        return DreamJob(
            tenant_key=str(row.get("tenant_key") or ""),
            session_key=str(row.get("session_key") or "") or None,
            namespace=str(metadata.get("namespace") or "default"),
            trigger=str(metadata.get("trigger") or payload.get("trigger") or "manual"),  # type: ignore[arg-type]
            reason=str(row.get("objective") or "dream task"),
            candidate_id=str(metadata.get("candidate_id") or row.get("run_id") or ""),
            source_id=metadata.get("source_id"),
            input_cursor=metadata.get("input_cursor"),
            payload=dict(payload),
            status=_dream_status(str(row.get("status") or "")),
            job_id=str(row.get("run_id") or ""),
            trace_id=str(row.get("trace_id") or ""),
            admitted_at_ms=_ms(row.get("created_at")) or now_ms(),
            queued_at_ms=_ms(row.get("started_at")) if _dream_status(str(row.get("status") or "")) == "queued" else None,
            started_at_ms=_ms(row.get("started_at")),
            completed_at_ms=_ms(row.get("completed_at")),
            last_error=row.get("last_error"),
        )

    async def update_job_status(
        self,
        job_id: str,
        status: DreamStatus,
        *,
        last_error: str | None = None,
        queued_at_ms: int | None = None,
        started_at_ms: int | None = None,
        completed_at_ms: int | None = None,
    ) -> None:
        await self._update_run_status(
            job_id,
            _run_status(status),
            last_error=last_error,
            queued_at_ms=queued_at_ms,
            started_at_ms=started_at_ms,
            completed_at_ms=completed_at_ms,
        )

    async def save_artifacts(self, artifacts: list[DreamArtifact]) -> None:
        if not artifacts:
            return
        run_rows: dict[str, dict[str, Any]] = {}
        for artifact in artifacts:
            if artifact.job_id not in run_rows:
                run_rows[artifact.job_id] = await self._get_run(artifact.job_id) or {}
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                for artifact in artifacts:
                    run = run_rows.get(artifact.job_id) or {}
                    tenant_key = str((run or {}).get("tenant_key") or "")
                    session_key = str((run or {}).get("session_key") or "")
                    owner_type = str((run or {}).get("owner_type") or "dream_job")
                    owner_id = str((run or {}).get("owner_id") or artifact.job_id)
                    metadata = dict(artifact.metadata or {})
                    metadata.setdefault("tenant_key", tenant_key)
                    metadata.setdefault("session_key", session_key)
                    metadata.setdefault("dream_job_id", artifact.job_id)
                    await cur.execute(
                        """
                        INSERT INTO nb_subagent_artifacts
                            (artifact_id, run_id, tenant_key, session_key, artifact_type,
                             status, owner_type, owner_id, artifact_key, content,
                             source_refs_json, metadata_json, created_at, updated_at, applied_at)
                        VALUES
                            (%s, %s, %s, %s, %s,
                             %s, %s, %s, %s, %s,
                             %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            status=VALUES(status),
                            content=VALUES(content),
                            metadata_json=VALUES(metadata_json),
                            updated_at=VALUES(updated_at),
                            applied_at=VALUES(applied_at)
                        """,
                        (
                            artifact.artifact_id,
                            artifact.job_id,
                            tenant_key,
                            session_key,
                            artifact.artifact_type,
                            artifact.status,
                            owner_type,
                            owner_id,
                            artifact.key,
                            artifact.content,
                            _json_or_none(metadata.get("source_refs") or {}),
                            _json_or_none(metadata),
                            _dt(artifact.created_at_ms),
                            _dt(artifact.updated_at_ms),
                            _dt(artifact.updated_at_ms) if artifact.status == "applied" else None,
                        ),
                    )

    async def running_scope_keys(self) -> set[str]:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT metadata_json
                    FROM nb_subagent_runs
                    WHERE subagent_name='dream'
                      AND status IN ('admitted', 'running')
                      AND JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.kind'))='dream_job'
                    """
                )
                rows = await cur.fetchall()
        scope_keys: set[str] = set()
        for (metadata_json,) in rows:
            metadata = _decode_json(metadata_json) or {}
            scope_key = str(metadata.get("scope_key") or "").strip()
            if scope_key:
                scope_keys.add(scope_key)
        return scope_keys

    async def last_succeeded_at_ms(self, scope_key: str) -> int | None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT completed_at
                    FROM nb_subagent_runs
                    WHERE subagent_name='dream'
                      AND status='completed'
                      AND JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.scope_key'))=%s
                    ORDER BY completed_at DESC
                    LIMIT 1
                    """,
                    (scope_key,),
                )
                row = await cur.fetchone()
        return _ms(row[0]) if row else None

    async def _upsert_run(
        self,
        *,
        run_id: str,
        tenant_key: str,
        session_key: str,
        status: str,
        owner_type: str,
        owner_id: str | None,
        runtime_task_id: str | None,
        trace_id: str | None,
        objective: str,
        input_refs: dict[str, Any],
        payload: dict[str, Any],
        metadata: dict[str, Any],
        created_at_ms: int,
    ) -> None:
        now = _dt(created_at_ms)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nb_subagent_runs
                        (run_id, tenant_key, session_key, subagent_name, run_mode, status,
                         owner_type, owner_id, runtime_task_id, trace_id, objective,
                         input_refs_json, payload_json, permission_profile_json,
                         expected_artifacts_json, metadata_json, created_at, updated_at)
                    VALUES
                        (%s, %s, %s, 'dream', 'dream', %s,
                         %s, %s, %s, %s, %s,
                         %s, %s, %s,
                         %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        status=VALUES(status),
                        runtime_task_id=COALESCE(VALUES(runtime_task_id), runtime_task_id),
                        trace_id=COALESCE(VALUES(trace_id), trace_id),
                        metadata_json=VALUES(metadata_json),
                        updated_at=VALUES(updated_at)
                    """,
                    (
                        run_id,
                        tenant_key,
                        session_key,
                        status,
                        owner_type,
                        owner_id,
                        runtime_task_id,
                        trace_id,
                        objective,
                        _json_or_none(input_refs),
                        _json_or_none(payload),
                        _json_or_none([
                            "read_memory",
                            "read_memory_ledger",
                            "read_runtime_task",
                            "read_document_version",
                            "read_session_messages",
                            "write_artifact",
                        ]),
                        _json_or_none(["memory_summary"]),
                        _json_or_none(metadata),
                        now,
                        now,
                    ),
                )

    async def _update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        last_error: str | None = None,
        updated_at_ms: int | None = None,
        queued_at_ms: int | None = None,
        started_at_ms: int | None = None,
        completed_at_ms: int | None = None,
    ) -> None:
        assignments = ["status=%s", "updated_at=%s"]
        params: list[Any] = [status, _dt(updated_at_ms or now_ms())]
        if queued_at_ms:
            assignments.append("started_at=COALESCE(started_at, %s)")
            params.append(_dt(queued_at_ms))
        if started_at_ms:
            assignments.append("started_at=COALESCE(started_at, %s)")
            params.append(_dt(started_at_ms))
        if completed_at_ms:
            assignments.append("completed_at=%s")
            params.append(_dt(completed_at_ms))
        if last_error is not None:
            assignments.append("last_error=%s")
            params.append(last_error)
        params.append(run_id)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    UPDATE nb_subagent_runs
                    SET {', '.join(assignments)}
                    WHERE run_id=%s
                    """,
                    tuple(params),
                )

    async def _get_run(self, run_id: str) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT *
                    FROM nb_subagent_runs
                    WHERE run_id=%s
                    LIMIT 1
                    """,
                    (run_id,),
                )
                row = await cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
        return dict(zip(cols, row)) if row else None


def _owner_type(source_id: str | None) -> str:
    source = str(source_id or "")
    if source.startswith("memledger_"):
        return "memory_ledger"
    return "dream_job"


def _input_refs(source_id: str | None, input_cursor: str | None) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    if source_id:
        if str(source_id).startswith("memledger_"):
            refs["memory_ledger_ids"] = [source_id]
        else:
            refs["source_id"] = source_id
    if input_cursor:
        refs["input_cursor"] = input_cursor
    return refs


def _run_status(status: str) -> str:
    if status == "succeeded":
        return "completed"
    if status == "queued":
        return "admitted"
    if status in {"candidate", "admitted", "running", "failed", "skipped", "cancelled", "superseded"}:
        return status
    return "failed"


def _dream_status(status: str) -> DreamStatus:
    if status == "completed":
        return "succeeded"
    if status in {"candidate", "admitted", "running", "failed", "skipped", "cancelled", "superseded"}:
        return status  # type: ignore[return-value]
    return "failed"


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _decode_json(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _dt(ms: int | None) -> datetime | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC).replace(tzinfo=None)


def _ms(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return int(dt.timestamp() * 1000)
    return None
