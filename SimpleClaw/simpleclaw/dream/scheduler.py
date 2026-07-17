"""Dream scheduler.

The scheduler converts dream candidates into admitted jobs and optionally
submits them to the runtime queue. It does not inspect business data.
"""

from __future__ import annotations

from dataclasses import dataclass

from simpleclaw.dream.policy import (
    DreamAdmissionContext,
    DreamAdmissionDecision,
    DreamAdmissionPolicy,
)
from simpleclaw.dream.protocol import DreamCandidate, DreamJob, now_ms
from simpleclaw.dream.store import DreamStore
from simpleclaw.runtime.task_protocol import BACKGROUND_STREAM, TaskStream


@dataclass(slots=True)
class DreamScheduleResult:
    decision: DreamAdmissionDecision
    job: DreamJob | None = None
    queue_message_id: str | None = None

    @property
    def admitted(self) -> bool:
        return self.decision.allowed and self.job is not None


class DreamScheduler:
    """Admission and enqueue coordinator for dream candidates."""

    def __init__(
        self,
        *,
        store: DreamStore,
        policy: DreamAdmissionPolicy | None = None,
        runtime: object | None = None,
        stream: TaskStream = BACKGROUND_STREAM,
        task_type: str = "dream",
    ) -> None:
        self._store = store
        self._policy = policy or DreamAdmissionPolicy()
        self._runtime = runtime
        self._stream = stream
        self._task_type = task_type

    async def schedule(
        self,
        candidate: DreamCandidate,
        *,
        context: DreamAdmissionContext | None = None,
    ) -> DreamScheduleResult:
        await self._store.save_candidate(candidate)

        ctx = context
        if ctx is None:
            ctx = DreamAdmissionContext(
                running_scope_keys=await self._store.running_scope_keys(),
                last_succeeded_at_ms=await self._store.last_succeeded_at_ms(candidate.scope_key),
            )
        elif not ctx.running_scope_keys:
            ctx.running_scope_keys.update(await self._store.running_scope_keys())
            if ctx.last_succeeded_at_ms is None:
                ctx.last_succeeded_at_ms = await self._store.last_succeeded_at_ms(candidate.scope_key)

        decision = self._policy.admit(candidate, ctx)
        if not decision.allowed:
            await self._store.update_candidate_status(candidate.candidate_id, "skipped")
            return DreamScheduleResult(decision=decision)

        await self._store.update_candidate_status(candidate.candidate_id, "admitted")
        job = DreamJob.from_candidate(candidate)
        await self._store.save_job(job)
        await self._store.update_candidate_status(candidate.candidate_id, "superseded")

        queue_message_id: str | None = None
        if self._runtime is not None:
            task = job.to_task_envelope(stream=self._stream, task_type=self._task_type)
            submit_task = getattr(self._runtime, "submit_task", None)
            if submit_task is None:
                raise RuntimeError("dream runtime must expose submit_task(task, ...)")
            queue_message_id = await submit_task(task, summary=f"dream admitted: {candidate.reason}")
            await self._store.update_job_status(job.job_id, "queued", queued_at_ms=now_ms())
            job = await self._store.get_job(job.job_id) or job

        return DreamScheduleResult(
            decision=decision,
            job=job,
            queue_message_id=queue_message_id,
        )
