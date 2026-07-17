"""Tests for Mojing device status queries."""

from __future__ import annotations

import json
import sys
import types
import unittest
from unittest.mock import patch

from Mojing.tools.device_status import DeviceStatusTool


class DeviceStatusToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_volume_alias_queries_current_volume(self) -> None:
        requests: list[dict] = []
        fake_httpx = _fake_httpx(requests, lambda body: {"fieldValue": 74})
        tool = DeviceStatusTool(api_url="https://example.test/agent/device/status")
        tool.set_context(device_id=33)

        self.assertEqual(tool.validate_params({"field": "volume"}), [])

        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            result = await tool.execute(field="volume")

        payload = json.loads(result.content)
        self.assertTrue(payload["ok"])
        self.assertEqual(requests[0]["json"]["field"], "current_volume")
        self.assertEqual(payload["field"], "current_volume")
        self.assertEqual(payload["field_value"], 74)
        self.assertIn("当前音量 74%", payload["user_visible_summary"])

    async def test_light_aliases_query_brightness_and_color_temp(self) -> None:
        requests: list[dict] = []

        def _payload(body: dict) -> dict:
            values = {
                "current_brightness": 2,
                "color_temp_percentage": 3,
            }
            return {"fieldValue": values[body["field"]]}

        fake_httpx = _fake_httpx(requests, _payload)
        tool = DeviceStatusTool(api_url="https://example.test/agent/device/status")
        tool.set_context(device_id=33)

        self.assertEqual(tool.validate_params({"field": "brightness"}), [])
        self.assertEqual(tool.validate_params({"field": "color_temp"}), [])

        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            brightness_result = await tool.execute(field="brightness")
            color_temp_result = await tool.execute(field="color_temp")

        brightness_payload = json.loads(brightness_result.content)
        color_temp_payload = json.loads(color_temp_result.content)
        self.assertEqual(requests[0]["json"]["field"], "current_brightness")
        self.assertEqual(brightness_payload["field"], "current_brightness")
        self.assertIn("当前灯光亮度 2%", brightness_payload["user_visible_summary"])
        self.assertEqual(requests[1]["json"]["field"], "color_temp_percentage")
        self.assertEqual(color_temp_payload["field"], "color_temp_percentage")
        self.assertIn("当前色温 3%", color_temp_payload["user_visible_summary"])

    async def test_full_status_summary_includes_volume_and_light_state(self) -> None:
        requests: list[dict] = []
        fake_httpx = _fake_httpx(
            requests,
            lambda body: {
                "allFields": {
                    "battery_level": 24,
                    "signal_strength": 90,
                    "status": 1,
                    "mqtt_status": 0,
                    "current_volume": 74,
                    "current_brightness": 2,
                    "color_temp_percentage": 3,
                }
            },
        )
        tool = DeviceStatusTool(api_url="https://example.test/agent/device/status")
        tool.set_context(device_id=33)

        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            result = await tool.execute()

        payload = json.loads(result.content)
        self.assertTrue(payload["ok"])
        self.assertNotIn("field", requests[0]["json"])
        self.assertIn("电量 24%", payload["user_visible_summary"])
        self.assertIn("音量 74%", payload["user_visible_summary"])
        self.assertIn("灯光亮度 2%", payload["user_visible_summary"])
        self.assertIn("色温 3%", payload["user_visible_summary"])


def _fake_httpx(requests: list[dict], payload_for_body):
    class _Response:
        def __init__(self, body: dict) -> None:
            self._body = body

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"code": 0, "data": self._body}

    class _Client:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def post(self, url: str, json: dict):
            requests.append({"url": url, "json": json})
            return _Response(payload_for_body(json))

    return types.SimpleNamespace(
        AsyncClient=_Client,
        TimeoutException=TimeoutError,
        RequestError=Exception,
        HTTPStatusError=Exception,
    )
