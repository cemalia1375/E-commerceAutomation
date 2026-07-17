"""Tests for Mojing device session dismissal."""

from __future__ import annotations

import json
import os
import sys
import types
import unittest
from unittest.mock import patch

from Mojing.config import make_device_dismiss_url
from Mojing.tools.device_dismiss import DeviceDismissTool


class DeviceDismissToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_starts_dismiss_flow_with_device_and_room_context(self) -> None:
        requests: list[dict] = []
        fake_httpx = _fake_httpx(requests)
        tool = DeviceDismissTool(api_url="https://example.test/agent/session/dismiss")
        tool.set_context(device_id="33", device_code="MJ02344544", room_id="G711ARoom33")

        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            result = await tool.execute()

        payload = json.loads(result.content)
        self.assertTrue(result.ok)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "dismiss_started")
        self.assertEqual(
            requests,
            [
                {
                    "url": "https://example.test/agent/session/dismiss",
                    "json": {
                        "deviceId": 33,
                        "deviceCode": "MJ02344544",
                        "roomId": "G711ARoom33",
                    },
                }
            ],
        )


class DeviceDismissConfigTest(unittest.TestCase):
    def test_default_dismiss_url_targets_backend_session_hook(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                make_device_dismiss_url(),
                "https://test.onrunlab.com/mojing/app-api/agent/session/dismiss",
            )


def _fake_httpx(requests: list[dict]):
    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"code": 0, "data": True}

    class _Client:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def post(self, url: str, json: dict):
            requests.append({"url": url, "json": json})
            return _Response()

    return types.SimpleNamespace(
        AsyncClient=_Client,
        TimeoutException=TimeoutError,
        RequestError=Exception,
        HTTPStatusError=Exception,
    )


if __name__ == "__main__":
    unittest.main()
