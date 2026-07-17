"""BackfillRunner 编排测试：全 fake 依赖，无 MySQL / TOS / 外部分析服务。"""
import unittest
from datetime import date, datetime
from typing import Any

from admin.lab.backfill import BackfillRunner, new_job_state
from simpleclaw.runtime.task_protocol import TaskExecutionResult


class _FakeUploader:
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def upload(self, *, key: str, data: bytes) -> str:
        self.keys.append(key)
        return f"https://bucket.test/{key}"


class _FakeImageRepo:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.succeeded: list[tuple[str, Any]] = []
        self.failed: list[tuple[str, str]] = []
        self.backdated: list[tuple[str, datetime]] = []

    async def create_job(self, **kwargs: Any) -> dict[str, Any]:
        job = {"job_id": f"job{len(self.created) + 1}", "image_id": f"img{len(self.created) + 1}", **kwargs}
        self.created.append(job)
        return job

    async def mark_succeeded(self, job_id: str, *, profile_id: Any = None, summary: str | None = None) -> None:
        self.succeeded.append((job_id, profile_id))

    async def mark_failed(self, job_id: str, *, error: str) -> None:
        self.failed.append((job_id, error))

    async def backdate_job(self, job_id: str, *, created_at: datetime) -> None:
        self.backdated.append((job_id, created_at))


class _FakeSkinProfileRepo:
    """find_profile_since 按 message_id 计数：第 1 次 None，第 2 次 pending，之后 synced。"""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.backdated: list[tuple[int, datetime]] = []
        self._next_profile_id = 100

    async def find_profile_since(self, *, tenant_key: str, since: Any,
                                 message_id: str | None = None, **kwargs: Any) -> dict[str, Any] | None:
        count = self.calls.get(message_id, 0) + 1
        self.calls[message_id] = count
        if count == 1:
            return None
        profile_id = self._next_profile_id + list(self.calls).index(message_id)
        return {
            "profile_id": profile_id,
            "message_id": message_id,
            "sync_status": "pending" if count == 2 else "synced",
        }

    async def backdate_profile(self, profile_id: int, *, created_at: datetime) -> None:
        self.backdated.append((profile_id, created_at))


class _FakeRuntime:
    def __init__(self) -> None:
        self.envelopes: list[Any] = []

    async def submit_task(self, envelope: Any, *, summary: str | None = None) -> str:
        self.envelopes.append(envelope)
        return f"q{len(self.envelopes)}"


def _make_runner(
    *,
    uploader: Any = None,
    image_repo: Any = None,
    skin_profile_repo: Any = None,
    runtime: Any = None,
    analysis_results: list[TaskExecutionResult] | None = None,
    analysis_log: list[Any] | None = None,
    profile_timeout_s: float = 5.0,
) -> BackfillRunner:
    results = list(analysis_results or [])

    async def analysis_execute(envelope: Any) -> TaskExecutionResult:
        if analysis_log is not None:
            analysis_log.append(envelope)
        if results:
            return results.pop(0)
        return TaskExecutionResult.wait_external(summary="accepted")

    return BackfillRunner(
        uploader=uploader or _FakeUploader(),
        image_repo=image_repo or _FakeImageRepo(),
        skin_profile_repo=skin_profile_repo or _FakeSkinProfileRepo(),
        runtime=runtime or _FakeRuntime(),
        analysis_execute=analysis_execute,
        profile_timeout_s=profile_timeout_s,
        sync_timeout_s=5.0,
        poll_interval_s=0.0,
    )


class BackfillRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_two_photos_full_flow(self) -> None:
        image_repo = _FakeImageRepo()
        profile_repo = _FakeSkinProfileRepo()
        runtime = _FakeRuntime()
        analysis_log: list[Any] = []
        runner = _make_runner(
            image_repo=image_repo,
            skin_profile_repo=profile_repo,
            runtime=runtime,
            analysis_log=analysis_log,
        )
        photos = [("day1.jpg", b"a"), ("day2.jpg", b"b")]
        days = [date(2026, 6, 9), date(2026, 6, 10)]
        state = new_job_state(user_id="test_u", photos=photos, days=days)

        await runner.run(state, user_id="test_u", photos=photos, days=days)

        self.assertEqual(state["state"], "done")
        self.assertEqual([it["stage"] for it in state["items"]], ["done", "done"])

        # 分析 payload 与真实链路一致：message_id == job_id，agent_state=image_full
        self.assertEqual(len(analysis_log), 2)
        for envelope, created in zip(analysis_log, image_repo.created):
            self.assertEqual(envelope.payload["message_id"], created["job_id"])
            self.assertEqual(envelope.payload["agent_state"], "image_full")

        # sync 走真实 SKIN_PROFILE_SYNC 任务，scope_key 与 monitor 同构
        self.assertEqual(len(runtime.envelopes), 2)
        sync = runtime.envelopes[0]
        self.assertEqual(str(sync.task_type), "skin_profile_sync")
        self.assertEqual(sync.scope_key, "postprocess:test_u:USER.md")

        # 回填日期：job 用 UTC 04:00，profile 用北京 12:00
        self.assertEqual(
            [dt for _, dt in image_repo.backdated],
            [datetime(2026, 6, 9, 4, 0), datetime(2026, 6, 10, 4, 0)],
        )
        self.assertEqual(
            [dt for _, dt in profile_repo.backdated],
            [datetime(2026, 6, 9, 12, 0), datetime(2026, 6, 10, 12, 0)],
        )

        # 画像成功标记带 profile_id
        self.assertEqual(len(image_repo.succeeded), 2)

    async def test_analysis_failure_does_not_stop_batch(self) -> None:
        image_repo = _FakeImageRepo()
        runner = _make_runner(
            image_repo=image_repo,
            analysis_results=[
                TaskExecutionResult.failed("boom", summary="upstream down"),
                TaskExecutionResult.wait_external(summary="accepted"),
            ],
        )
        photos = [("p1.jpg", b"a"), ("p2.jpg", b"b")]
        days = [date(2026, 6, 9), date(2026, 6, 10)]
        state = new_job_state(user_id="test_u", photos=photos, days=days)

        await runner.run(state, user_id="test_u", photos=photos, days=days)

        self.assertEqual(state["state"], "done")
        self.assertEqual(state["items"][0]["stage"], "failed")
        self.assertTrue(state["items"][0]["error"])
        self.assertEqual(state["items"][1]["stage"], "done")
        # 失败的 job 被 mark_failed
        self.assertEqual(len(image_repo.failed), 1)

    async def test_profile_timeout_marks_item_failed(self) -> None:
        class _NeverProfileRepo(_FakeSkinProfileRepo):
            async def find_profile_since(self, **kwargs: Any) -> None:
                return None

        runner = _make_runner(
            skin_profile_repo=_NeverProfileRepo(),
            profile_timeout_s=0.05,
        )
        photos = [("p1.jpg", b"a")]
        days = [date(2026, 6, 10)]
        state = new_job_state(user_id="test_u", photos=photos, days=days)

        await runner.run(state, user_id="test_u", photos=photos, days=days)

        self.assertEqual(state["items"][0]["stage"], "failed")
        self.assertIn("画像", state["items"][0]["error"])
