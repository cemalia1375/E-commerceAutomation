from __future__ import annotations

import unittest
from unittest.mock import patch

from simpleclaw.runtime.task_protocol import TaskEnvelope
from Mojing.runtime.executors import make_image_analysis_executor


class _ImageRepo:
    def __init__(self) -> None:
        self.running: list[str] = []
        self.wait_external: list[tuple[str, str | None, object]] = []
        self.failed: list[tuple[str, str]] = []

    async def mark_running(self, job_id: str) -> None:
        self.running.append(job_id)

    async def mark_wait_external(self, job_id: str, *, external_job_id=None, response=None) -> None:
        self.wait_external.append((job_id, external_job_id, response))

    async def mark_failed(self, job_id: str, *, error: str) -> None:
        self.failed.append((job_id, error))


class _Response:
    def __init__(self, status_code: int, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class _Client:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def post(self, *_args, **_kwargs):
        if self._error is not None:
            raise self._error
        return self._response


def _task() -> TaskEnvelope:
    return TaskEnvelope(
        task_type="image_analysis",
        payload={"job_id": "job-1", "tenant_key": "tenant-1", "image": "https://example.com/a.png"},
        stream="image_analysis",
        tenant_key="tenant-1",
    )


class ImageAnalysisExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def test_marks_running_and_wait_external_on_http_success(self) -> None:
        repo = _ImageRepo()
        executor = make_image_analysis_executor("https://example.com/hook", image_repo=repo)

        with patch(
            "Mojing.runtime.executors.httpx.AsyncClient",
            return_value=_Client(_Response(200, {"job_id": "external-1"})),
        ):
            result = await executor(_task())

        self.assertEqual(result.status, "wait_external")
        self.assertEqual(repo.running, ["job-1"])
        self.assertEqual(repo.wait_external[0][0], "job-1")
        self.assertEqual(repo.wait_external[0][1], "external-1")
        self.assertEqual(repo.failed, [])

    async def test_marks_failed_on_http_error(self) -> None:
        repo = _ImageRepo()
        executor = make_image_analysis_executor("https://example.com/hook", image_repo=repo)

        with patch(
            "Mojing.runtime.executors.httpx.AsyncClient",
            return_value=_Client(_Response(500, {"error": "bad"})),
        ):
            result = await executor(_task())

        self.assertEqual(result.status, "failed")
        self.assertEqual(repo.running, ["job-1"])
        self.assertEqual(repo.failed, [("job-1", "HTTP 500")])


if __name__ == "__main__":
    unittest.main()
