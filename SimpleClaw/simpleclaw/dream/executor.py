"""Dream executor adapter for runtime workers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from simpleclaw.dream.protocol import DreamJob, DreamResult, now_ms
from simpleclaw.dream.store import DreamStore
from simpleclaw.runtime.task_protocol import TaskEnvelope, TaskExecutionResult


class DreamRunner(Protocol):
    async def __call__(self, job: DreamJob) -> DreamResult:
        ...


class DreamExecutor:
    """Execute dream runtime tasks using an application-provided runner."""

    def __init__(
        self,
        *,
        store: DreamStore,
        runner: DreamRunner | Callable[[DreamJob], Awaitable[DreamResult]],
    ) -> None:
        self._store = store
        self._runner = runner

    async def __call__(self, task: TaskEnvelope) -> TaskExecutionResult:
        return await self.execute(task)

    async def execute(self, task: TaskEnvelope) -> TaskExecutionResult:
        job = await self._store.get_job(task.task_id)
        if job is None:
            job = DreamJob.from_task_envelope(task)
            await self._store.save_job(job)

        await self._store.update_job_status(job.job_id, "running", started_at_ms=now_ms())
        try:
            result = await self._runner(job)
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            await self._store.update_job_status(
                job.job_id,
                "failed",
                last_error=error,
                completed_at_ms=now_ms(),
            )
            return TaskExecutionResult.failed(error, summary="dream failed")

        if result.artifacts:
            await self._store.save_artifacts(result.artifacts)

        if result.status == "succeeded":
            await self._store.update_job_status(
                job.job_id,
                "succeeded",
                completed_at_ms=result.completed_at_ms,
            )
            return TaskExecutionResult.succeeded(
                summary=result.summary or "dream succeeded",
                details=result.to_dict(),
            )
        if result.status == "skipped":
            await self._store.update_job_status(
                job.job_id,
                "skipped",
                completed_at_ms=result.completed_at_ms,
            )
            return TaskExecutionResult.noop(
                summary=result.summary or "dream skipped",
                details=result.to_dict(),
            )

        error = result.last_error or result.summary or "dream failed"
        await self._store.update_job_status(
            job.job_id,
            "failed",
            last_error=error,
            completed_at_ms=result.completed_at_ms,
        )
        return TaskExecutionResult.failed(
            error,
            summary=result.summary or "dream failed",
            details=result.to_dict(),
        )
