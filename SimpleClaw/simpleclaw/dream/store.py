"""Dream store contracts and in-memory implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from typing import Any

from simpleclaw.dream.protocol import (
    DreamArtifact,
    DreamCandidate,
    DreamJob,
    DreamStatus,
    now_ms,
)


class DreamStore(ABC):
    """Persistence contract for dream governance facts."""

    @abstractmethod
    async def save_candidate(self, candidate: DreamCandidate) -> None:
        ...

    @abstractmethod
    async def update_candidate_status(
        self,
        candidate_id: str,
        status: DreamStatus,
        *,
        updated_at_ms: int | None = None,
    ) -> None:
        ...

    @abstractmethod
    async def save_job(self, job: DreamJob) -> None:
        ...

    @abstractmethod
    async def get_job(self, job_id: str) -> DreamJob | None:
        ...

    @abstractmethod
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
        ...

    @abstractmethod
    async def save_artifacts(self, artifacts: list[DreamArtifact]) -> None:
        ...

    @abstractmethod
    async def running_scope_keys(self) -> set[str]:
        ...

    @abstractmethod
    async def last_succeeded_at_ms(self, scope_key: str) -> int | None:
        ...


class InMemoryDreamStore(DreamStore):
    """Small in-memory store for tests and local runtime wiring."""

    def __init__(self) -> None:
        self.candidates: dict[str, DreamCandidate] = {}
        self.jobs: dict[str, DreamJob] = {}
        self.artifacts: dict[str, DreamArtifact] = {}

    async def save_candidate(self, candidate: DreamCandidate) -> None:
        self.candidates[candidate.candidate_id] = candidate

    async def update_candidate_status(
        self,
        candidate_id: str,
        status: DreamStatus,
        *,
        updated_at_ms: int | None = None,
    ) -> None:
        candidate = self.candidates.get(candidate_id)
        if candidate is None:
            return
        self.candidates[candidate_id] = replace(
            candidate,
            status=status,
            updated_at_ms=updated_at_ms or now_ms(),
        )

    async def save_job(self, job: DreamJob) -> None:
        self.jobs[job.job_id] = job

    async def get_job(self, job_id: str) -> DreamJob | None:
        return self.jobs.get(job_id)

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
        job = self.jobs.get(job_id)
        if job is None:
            return
        self.jobs[job_id] = replace(
            job,
            status=status,
            last_error=last_error if last_error is not None else job.last_error,
            queued_at_ms=queued_at_ms if queued_at_ms is not None else job.queued_at_ms,
            started_at_ms=started_at_ms if started_at_ms is not None else job.started_at_ms,
            completed_at_ms=completed_at_ms if completed_at_ms is not None else job.completed_at_ms,
        )

    async def save_artifacts(self, artifacts: list[DreamArtifact]) -> None:
        for artifact in artifacts:
            self.artifacts[artifact.artifact_id] = artifact

    async def running_scope_keys(self) -> set[str]:
        return {
            job.scope_key
            for job in self.jobs.values()
            if job.status in {"admitted", "queued", "running"}
        }

    async def last_succeeded_at_ms(self, scope_key: str) -> int | None:
        completed = [
            int(job.completed_at_ms or 0)
            for job in self.jobs.values()
            if job.scope_key == scope_key and job.status == "succeeded" and job.completed_at_ms
        ]
        return max(completed) if completed else None
