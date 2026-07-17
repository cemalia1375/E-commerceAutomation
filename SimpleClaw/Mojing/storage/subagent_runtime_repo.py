"""MySQL persistence for governed subagent runs and artifacts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from simpleclaw.subagent.runtime import (
    SubagentArtifact,
    SubagentRunRequest,
    SubagentRunResult,
    SubagentRunStatus,
    now_ms,
)
from Mojing.storage.database import Database


class SubagentRuntimeRepository:
    """Persist subagent runtime governance records."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def create_run(
        self,
        request: SubagentRunRequest,
        *,
        runtime_task_id: str | None = None,
    ) -> None:
        now = _dt(request.created_at_ms)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nb_subagent_runs
                        (run_id, tenant_key, session_key, subagent_name, run_mode, status,
                         owner_type, owner_id, runtime_task_id, trace_id, objective,
                         input_refs_json, payload_json, permission_profile_json,
                         expected_artifacts_json, summary, reply_text, last_error,
                         metadata_json, created_at, updated_at)
                    VALUES
                        (%s, %s, %s, %s, %s, %s,
                         %s, %s, %s, %s, %s,
                         %s, %s, %s,
                         %s, %s, %s, %s,
                         %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        status=VALUES(status),
                        runtime_task_id=COALESCE(VALUES(runtime_task_id), runtime_task_id),
                        updated_at=VALUES(updated_at)
                    """,
                    (
                        request.run_id,
                        request.tenant_key,
                        request.session_key,
                        request.subagent_name,
                        request.run_mode,
                        request.status,
                        request.owner_type,
                        request.owner_id,
                        runtime_task_id,
                        request.trace_id,
                        request.objective,
                        _json_or_none(request.input_refs),
                        _json_or_none(request.payload),
                        _json_or_none(request.permission_profile),
                        _json_or_none(request.expected_artifacts),
                        None,
                        None,
                        None,
                        _json_or_none({"dedupe_key": request.effective_dedupe_key}),
                        now,
                        now,
                    ),
                )

    async def mark_run_status(
        self,
        run_id: str,
        status: SubagentRunStatus,
        *,
        summary: str | None = None,
        last_error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        run_id = str(run_id or "").strip()
        if not run_id:
            return
        assignments = ["status=%s", "updated_at=%s"]
        params: list[Any] = [status, _dt(now_ms())]
        if status == "running":
            assignments.append("started_at=COALESCE(started_at, %s)")
            params.append(_dt(now_ms()))
        if status in {"completed", "failed", "skipped", "cancelled", "superseded"}:
            assignments.append("completed_at=COALESCE(completed_at, %s)")
            params.append(_dt(now_ms()))
        if summary is not None:
            assignments.append("summary=%s")
            params.append(summary)
        if last_error is not None:
            assignments.append("last_error=%s")
            params.append(last_error)
        if metadata is not None:
            assignments.append("metadata_json=%s")
            params.append(_json_or_none(metadata))
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

    async def complete_run(self, result: SubagentRunResult) -> None:
        status = result.status
        summary = result.summary
        metadata = dict(result.metadata or {})
        if result.read_refs:
            metadata["read_refs"] = result.read_refs
        if result.write_refs:
            metadata["write_refs"] = result.write_refs
        if result.tool_invocations:
            metadata["tool_invocations"] = result.tool_invocations
        if result.side_effects:
            metadata["side_effects"] = result.side_effects
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_subagent_runs
                    SET status=%s,
                        summary=%s,
                        reply_text=%s,
                        last_error=%s,
                        metadata_json=%s,
                        completed_at=%s,
                        updated_at=%s
                    WHERE run_id=%s
                    """,
                    (
                        status,
                        summary,
                        result.reply_text or None,
                        result.last_error,
                        _json_or_none(metadata),
                        _dt(result.completed_at_ms),
                        _dt(now_ms()),
                        result.run_id,
                    ),
                )
        if result.artifacts:
            await self.save_artifacts(result.artifacts)

    async def save_artifacts(self, artifacts: list[SubagentArtifact]) -> None:
        if not artifacts:
            return
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                for artifact in artifacts:
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
                            source_refs_json=VALUES(source_refs_json),
                            updated_at=VALUES(updated_at),
                            applied_at=VALUES(applied_at)
                        """,
                        (
                            artifact.artifact_id,
                            artifact.run_id,
                            str(artifact.metadata.get("tenant_key") or ""),
                            str(artifact.metadata.get("session_key") or ""),
                            artifact.artifact_type,
                            artifact.status,
                            artifact.owner_type,
                            artifact.owner_id,
                            artifact.key,
                            artifact.content,
                            _json_or_none(artifact.source_refs),
                            _json_or_none(artifact.metadata),
                            _dt(artifact.created_at_ms),
                            _dt(artifact.updated_at_ms),
                            _dt(artifact.updated_at_ms) if artifact.status == "applied" else None,
                        ),
                    )


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _dt(ms: int | None) -> datetime | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC).replace(tzinfo=None)
