"""Tests for Mojing device command integration."""

from __future__ import annotations

import json
import sys
import types
import unittest
from unittest.mock import patch

from Mojing.api.request_utils import (
    normalize_origin_session_key,
    normalize_session_key,
    resolve_agent_chat_context,
    resolve_volcano_context,
)
from Mojing.agent.capabilities import AgentCapabilities, capabilities_from_device_context
from Mojing.agent.main_agent import MainAgent
from Mojing.runtime.photo_capture import PhotoCaptureCoordinator
from Mojing.tools.device_command import DeviceCommandTool, normalize_device_command
from simpleclaw.tools.base import Tool, ToolResult
from simpleclaw.tools.registry import ToolRegistry


class _NamedTool(Tool):
    description = "test tool"
    parameters = {"type": "object", "properties": {}}

    def __init__(self, name: str) -> None:
        self.name = name

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(content="ok")


class _MainAgentForDeviceTest(MainAgent):
    def _make_tool_lifecycle(self):
        return None

    def make_dynamic_context_providers(self, tenant_key: str):
        del tenant_key
        return []

    def make_attention_providers(self, tenant_key: str):
        del tenant_key
        return []


class DeviceCommandToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_device_returns_user_visible_failure(self) -> None:
        tool = DeviceCommandTool(api_url="http://example.invalid/device")

        result = await tool.execute(command="volume_up")
        payload = json.loads(result.content)

        self.assertFalse(result.ok)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "missing_device")
        self.assertIn("缺少设备信息", payload["error"])

    async def test_context_accepts_device_id_and_code(self) -> None:
        tool = DeviceCommandTool(api_url="http://example.invalid/device")
        tool.set_context(device_id="123", device_code="abc")

        self.assertEqual(tool._device_id, 123)
        self.assertEqual(tool._device_code, "abc")

    def test_validates_supported_command(self) -> None:
        tool = DeviceCommandTool(api_url="http://example.invalid/device")

        self.assertEqual(tool.validate_params({"command": "volume_up"}), [])
        self.assertIn("unsupported command", tool.validate_params({"command": "bad"})[0])

    def test_validate_accepts_semantic_alias(self) -> None:
        tool = DeviceCommandTool(api_url="http://example.invalid/device")
        self.assertEqual(tool.validate_params({"command": "light_on"}), [])

    async def test_unknown_light_preset_mode_fails_without_commute_fallback(self) -> None:
        tool = DeviceCommandTool(api_url="http://example.invalid/device")
        tool.set_context(device_id=123)

        result = await tool.execute(command="light_preset_mode", params={"mode": "moonlight"})
        payload = json.loads(result.content)

        self.assertFalse(result.ok)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "unsupported_preset_mode")
        self.assertIn("moonlight", payload["error"])

    async def test_capture_photo_disabled_blocks_hardware_call(self) -> None:
        tool = DeviceCommandTool(api_url="http://example.invalid/device")
        tool.set_context(device_id="123", capture_photo_enabled=False)

        result = await tool.execute(command="capture_photo")
        payload = json.loads(result.content)

        self.assertFalse(result.ok)
        self.assertEqual(payload["status"], "capture_photo_disabled")

    async def test_capture_photo_returns_photo_ready_when_photo_arrives(self) -> None:
        coordinator = PhotoCaptureCoordinator()

        class _Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"code": 0, "data": {"photoId": "photo_1"}}

        class _Client:
            def __init__(self, timeout: float) -> None:
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args) -> None:
                return None

            async def post(self, url: str, json: dict):
                async def _return_photo() -> None:
                    await coordinator.resolve_photo(
                        capture_request_id=json["captureRequestId"],
                        photo_id="photo_1",
                        photo_url="https://example.test/photo.jpg",
                    )

                import asyncio

                asyncio.create_task(_return_photo())
                return _Response()

        fake_httpx = types.SimpleNamespace(
            AsyncClient=_Client,
            TimeoutException=TimeoutError,
            RequestError=Exception,
            HTTPStatusError=Exception,
        )
        tool = DeviceCommandTool(
            api_url="http://example.test/agent/device/command",
            photo_capture_coordinator=coordinator,
            photo_wait_timeout_s=0.5,
        )
        tool.set_context(
            tenant_key="tenant-1",
            session_key="main:session-1",
            device_id=1,
        )

        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            result = await tool.execute(command="capture_photo")

        payload = json.loads(result.content)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "photo_ready")
        self.assertEqual(payload["photoId"], "photo_1")
        self.assertEqual(payload["photoUrl"], "https://example.test/photo.jpg")

    async def test_capture_photo_returns_failed_when_hardware_reports_failure(self) -> None:
        coordinator = PhotoCaptureCoordinator()

        class _Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"code": 0, "data": {"photoId": "photo_failed_1"}}

        class _Client:
            def __init__(self, timeout: float) -> None:
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args) -> None:
                return None

            async def post(self, url: str, json: dict):
                async def _return_failure() -> None:
                    await coordinator.resolve_failure(
                        capture_request_id=json["captureRequestId"],
                        photo_id="photo_failed_1",
                        reason="拍照失败",
                    )

                import asyncio

                asyncio.create_task(_return_failure())
                return _Response()

        fake_httpx = types.SimpleNamespace(
            AsyncClient=_Client,
            TimeoutException=TimeoutError,
            RequestError=Exception,
            HTTPStatusError=Exception,
        )
        tool = DeviceCommandTool(
            api_url="http://example.test/agent/device/command",
            photo_capture_coordinator=coordinator,
            photo_wait_timeout_s=0.5,
        )
        tool.set_context(tenant_key="tenant-1", session_key="main:session-1", device_id=1)

        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            result = await tool.execute(command="capture_photo")

        payload = json.loads(result.content)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "photo_failed")
        self.assertEqual(payload["photoId"], "photo_failed_1")
        self.assertEqual(payload["reason"], "拍照失败")

    async def test_capture_photo_returns_timeout_when_photo_times_out(self) -> None:
        coordinator = PhotoCaptureCoordinator()

        class _Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"code": 0, "data": {"photoId": "photo_2"}}

        class _Client:
            def __init__(self, timeout: float) -> None:
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args) -> None:
                return None

            async def post(self, url: str, json: dict):
                return _Response()

        fake_httpx = types.SimpleNamespace(
            AsyncClient=_Client,
            TimeoutException=TimeoutError,
            RequestError=Exception,
            HTTPStatusError=Exception,
        )
        tool = DeviceCommandTool(
            api_url="http://example.test/agent/device/command",
            photo_capture_coordinator=coordinator,
            photo_wait_timeout_s=0.01,
        )
        tool.set_context(tenant_key="tenant-1", session_key="main:session-1", device_id=1)

        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            result = await tool.execute(command="capture_photo")

        payload = json.loads(result.content)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "photo_timeout")
        self.assertEqual(payload["photoId"], "photo_2")
        self.assertEqual(payload["status"], "timeout")

    async def test_late_photo_after_timeout_is_recorded_only(self) -> None:
        coordinator = PhotoCaptureCoordinator()
        await coordinator.register(
            capture_request_id="cap_timeout",
            tenant_key="tenant-1",
            session_key="main:session-1",
        )
        await coordinator.attach_photo_id("cap_timeout", "photo_late_1")

        payload = await coordinator.wait_for_photo("cap_timeout", timeout_s=0.01)
        self.assertIsNone(payload)

        result = await coordinator.resolve_photo(
            capture_request_id="cap_timeout",
            photo_id="photo_late_1",
            photo_url="https://example.test/late.jpg",
        )

        self.assertEqual(result["action"], "recorded_only")
        self.assertEqual(result["reason"], "waiter_timeout")
        self.assertEqual(result["photoUrl"], "https://example.test/late.jpg")


class DeviceCommandNormalizeTest(unittest.TestCase):
    def test_volume_set_renames_volume_to_percentage(self) -> None:
        cmd, params = normalize_device_command("volume_set", {"volume": 30})
        self.assertEqual(cmd, "volume_set")
        self.assertEqual(params["percentage"], 30)
        self.assertNotIn("volume", params)

    def test_light_brightness_set_renames_brightness(self) -> None:
        cmd, params = normalize_device_command("light_brightness_set", {"brightness": 75})
        self.assertEqual(params["percentage"], 75)

    def test_light_on_alias(self) -> None:
        cmd, params = normalize_device_command("light_on", None)
        self.assertEqual(cmd, "light_brightness_set")
        self.assertEqual(params["percentage"], 80)

    def test_light_off_alias(self) -> None:
        cmd, params = normalize_device_command("light_off", None)
        self.assertEqual(cmd, "light_brightness_set")
        self.assertEqual(params["percentage"], 0)

    def test_warm_tone_alias(self) -> None:
        cmd, params = normalize_device_command("warm_tone", None)
        self.assertEqual(cmd, "light_color_temp")
        self.assertEqual(params["mode"], "warm")

    def test_light_preset_aliases_from_user_language(self) -> None:
        cases = {
            "晚宴光": "banquet",
            "晚艳光": "banquet",
            "evening_glam": "banquet",
            "直播光": "streaming",
            "live": "streaming",
            "约会光": "dating",
            "date": "dating",
            "date_light": "dating",
            "卸妆光": "makeup_removal",
            "remove_makeup_light": "makeup_removal",
            "阅读光": "reading",
            "reading_mode": "reading",
        }

        for raw_mode, expected in cases.items():
            with self.subTest(raw_mode=raw_mode):
                cmd, params = normalize_device_command("light_preset_mode", {"mode": raw_mode})
                self.assertEqual(cmd, "light_preset_mode")
                self.assertEqual(params["mode"], expected)
                self.assertEqual(
                    DeviceCommandTool(api_url="http://example.invalid/device").validate_params(
                        {"command": "light_preset_mode", "params": {"mode": raw_mode}}
                    ),
                    [],
                )

    def test_color_temp_mode_field_alias(self) -> None:
        cmd, params = normalize_device_command("light_color_temp", {"colorTempMode": "warm"})
        self.assertEqual(params["mode"], "warm")

    def test_color_temp_chinese_mode(self) -> None:
        cmd, params = normalize_device_command("light_color_temp", {"mode": "冷色调"})
        self.assertEqual(params["mode"], "cool")


class DeviceContextParsingTest(unittest.TestCase):
    def test_agent_chat_reads_device_fields(self) -> None:
        ctx = resolve_agent_chat_context(
            {
                "tenant_key": "tenant-1",
                "message": "把灯调亮一点",
                "user_context": {"deviceCode": "mirror-1"},
            }
        )

        self.assertEqual(ctx["device_code"], "mirror-1")

    def test_volcano_custom_reads_camel_case_device_fields(self) -> None:
        ctx = resolve_volcano_context(
            {
                "custom": json.dumps({"user_id": "u1", "deviceId": "42", "deviceCode": "mirror-42"}),
                "messages": [{"role": "user", "content": "拍一张照片"}],
            },
            {},
        )

        self.assertEqual(ctx["device_id"], "42")
        self.assertEqual(ctx["device_code"], "mirror-42")

    def test_volcano_internal_photo_return_disables_capture_photo(self) -> None:
        ctx = resolve_volcano_context(
            {
                "custom": json.dumps({"user_id": "u1", "deviceId": "42"}),
                "messages": [{"role": "user", "content": "【设备照片返回】刚才拍照结果回来了。"}],
            },
            {},
        )

        self.assertFalse(ctx["capture_photo_enabled"])

    def test_volcano_custom_keeps_internal_main_session_id(self) -> None:
        ctx = resolve_volcano_context(
            {
                "custom": json.dumps({"user_id": "334", "session_id": "main:session_334_x"}),
                "messages": [{"role": "user", "content": "拍一张照片"}],
            },
            {},
        )

        self.assertEqual(ctx["tenant_key"], "334")
        self.assertEqual(ctx["session_key"], "main:session_334_x")
        self.assertEqual(ctx["origin_session_key"], "main:session_334_x")

    def test_agent_chat_context_wraps_frontend_session_id_as_main_session(self) -> None:
        ctx = resolve_agent_chat_context(
            {
                "user_id": "334",
                "user_context": {"session_id": "session_334_1777472228080"},
                "message": "拍一张照片",
            }
        )

        self.assertEqual(ctx["tenant_key"], "334")
        self.assertEqual(ctx["session_key"], "main:session_334_1777472228080")
        self.assertEqual(ctx["origin_session_key"], "main:session_334_1777472228080")

    def test_agent_chat_context_wraps_numeric_tenant_frontend_session(self) -> None:
        ctx = resolve_agent_chat_context(
            {
                "user_id": "290",
                "session_id": "session_290_1770106399774_U992Dj",
                "message": "拍一张照片",
            }
        )

        self.assertEqual(ctx["tenant_key"], "290")
        self.assertEqual(ctx["session_key"], "main:session_290_1770106399774_U992Dj")
        self.assertEqual(ctx["origin_session_key"], "main:session_290_1770106399774_U992Dj")

    def test_agent_chat_context_does_not_double_wrap_internal_session_key(self) -> None:
        ctx = resolve_agent_chat_context(
            {
                "user_id": "334",
                "session_id": "skin_diary:334",
                "message": "看看肌肤日记",
            }
        )

        self.assertEqual(ctx["session_key"], "skin_diary:334")
        self.assertEqual(ctx["origin_session_key"], "main:334")

    def test_agent_chat_subagent_context_uses_explicit_parent_main_session(self) -> None:
        ctx = resolve_agent_chat_context(
            {
                "user_id": "290",
                "session_id": "skin_diary:290",
                "origin_session_id": "session_290_1770106399774_U992Dj",
                "message": "看看肌肤日记",
            }
        )

        self.assertEqual(ctx["session_key"], "skin_diary:290")
        self.assertEqual(ctx["origin_session_key"], "main:session_290_1770106399774_U992Dj")

    def test_volcano_subagent_context_uses_explicit_parent_main_session(self) -> None:
        ctx = resolve_volcano_context(
            {
                "custom": json.dumps({
                    "user_id": "290",
                    "session_id": "deep_report:290",
                    "originSessionId": "session_290_1770106399774_U992Dj",
                }),
                "messages": [{"role": "user", "content": "解读报告"}],
            },
            {},
        )

        self.assertEqual(ctx["session_key"], "deep_report:290")
        self.assertEqual(ctx["origin_session_key"], "main:session_290_1770106399774_U992Dj")

    def test_event_stream_can_share_session_normalization_contract(self) -> None:
        self.assertEqual(
            normalize_session_key("session_290_1770106399774_U992Dj", "290"),
            "main:session_290_1770106399774_U992Dj",
        )
        self.assertEqual(
            normalize_session_key("main:session_290_1770106399774_U992Dj", "290"),
            "main:session_290_1770106399774_U992Dj",
        )
        self.assertEqual(normalize_session_key("skin_diary:290", "290"), "skin_diary:290")
        self.assertEqual(normalize_session_key("deep_report:290", "290"), "deep_report:290")

    def test_origin_session_normalization_always_targets_main_session(self) -> None:
        self.assertEqual(
            normalize_origin_session_key(
                "session_290_1770106399774_U992Dj",
                "290",
                session_key="skin_diary:290",
            ),
            "main:session_290_1770106399774_U992Dj",
        )
        self.assertEqual(
            normalize_origin_session_key("", "290", session_key="main:session_290_x"),
            "main:session_290_x",
        )
        self.assertEqual(
            normalize_origin_session_key("", "290", session_key="skin_diary:290"),
            "main:290",
        )


class DeviceCapabilityRegistrationTest(unittest.IsolatedAsyncioTestCase):
    def _agent(self) -> MainAgent:
        return _MainAgentForDeviceTest(
            db=None,
            document_repo=None,
            image_repo=None,
            base_registry=ToolRegistry(),
            tool_factories=[lambda _: _NamedTool("regular_tool")],
            device_tool_factories=[lambda _: _NamedTool("device_command")],
        )

    def test_device_capability_requires_device_context(self) -> None:
        self.assertFalse(capabilities_from_device_context().device_enabled)
        self.assertTrue(capabilities_from_device_context(device_code="mirror-1").device_enabled)
        self.assertTrue(capabilities_from_device_context(device_id="42").device_enabled)

    def test_device_tool_is_registered_only_when_capability_enabled(self) -> None:
        agent = self._agent()

        without_device = agent.make_tool_registry(
            "u1",
            capabilities=AgentCapabilities(device_enabled=False),
        )
        with_device = agent.make_tool_registry(
            "u1",
            capabilities=AgentCapabilities(device_enabled=True),
        )

        self.assertIn("regular_tool", without_device.tool_names)
        self.assertNotIn("device_command", without_device.tool_names)
        self.assertIn("regular_tool", with_device.tool_names)
        self.assertIn("device_command", with_device.tool_names)

    async def test_device_prompt_is_selected_by_prompt_surface(self) -> None:
        agent = self._agent()

        app_builder = await agent.make_context_builder(
            "u1",
            capabilities=AgentCapabilities(device_enabled=False),
        )
        device_builder = await agent.make_context_builder(
            "u1",
            capabilities=AgentCapabilities(device_enabled=True, prompt_surface="device"),
        )

        app_messages = await app_builder.build([], query="拍一张照片")
        device_messages = await device_builder.build([], query="拍一张照片")

        app_prompt = app_messages[0]["content"]
        device_prompt = device_messages[0]["content"]
        self.assertNotIn("device_command", app_prompt)
        self.assertNotIn("硬件魔镜主 Agent", app_prompt)
        self.assertIn("device_command", device_prompt)
        self.assertIn("硬件魔镜主 Agent", device_prompt)


if __name__ == "__main__":
    unittest.main()
