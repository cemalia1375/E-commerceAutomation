"""Device dismiss hook for user-initiated goodbye / sleep / shutdown."""

from __future__ import annotations

import json
import logging
from typing import Any

try:
    from loguru import logger
except ImportError:  # pragma: no cover
    class _FallbackLogger:
        _logger = logging.getLogger(__name__)

        def info(self, message: str, *args: Any) -> None:
            self._logger.info(message.format(*args))

        def warning(self, message: str, *args: Any) -> None:
            self._logger.warning(message.format(*args))

    logger = _FallbackLogger()

from simpleclaw.tools.base import Tool, ToolResult


class DeviceDismissTool(Tool):
    """Trigger the backend room-exit flow when the user clearly dismisses the device."""

    name = "device_dismiss"
    description = (
        "当用户明确表示结束对话、让你退下、再见、拜拜、关机、休眠、不聊了、我走了等意图时调用。"
        "触发后后端会播放告别语并退出 RTC 房间，无需再调用 device_command。"
        "不要在普通闲聊结束或用户只是沉默时调用。"
    )
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    needs_followup = True
    tool_category = "sync_write"

    def __init__(self, *, api_url: str, timeout_s: float = 10.0) -> None:
        self._api_url = api_url.strip()
        self._timeout_s = max(1.0, float(timeout_s))
        self._device_id: int | None = None
        self._device_code: str | None = None
        self._room_id: str | None = None

    def set_context(
        self,
        *,
        device_id: int | str | None = None,
        device_code: str | None = None,
        room_id: str | None = None,
        **_: Any,
    ) -> None:
        self._device_id = _coerce_device_id(device_id)
        dc = str(device_code or "").strip()
        self._device_code = dc or None
        rid = str(room_id or "").strip()
        self._room_id = rid or None

    async def execute(self) -> ToolResult:
        if not self._device_id and not self._device_code:
            return _failure_result(
                "missing_device",
                "缺少设备信息，无法触发退下流程。",
                message_focus="没有拿到设备信息。请自然告诉用户这次没能处理退下，让她稍后再试。",
            )

        body: dict[str, Any] = {}
        if self._device_id is not None:
            body["deviceId"] = self._device_id
        if self._device_code:
            body["deviceCode"] = self._device_code
        if self._room_id:
            body["roomId"] = self._room_id

        logger.info(
            "device_dismiss POST {} deviceId={} deviceCode={!r} roomId={!r}",
            self._api_url,
            body.get("deviceId"),
            body.get("deviceCode"),
            body.get("roomId"),
        )

        try:
            import httpx
        except ImportError:
            return _failure_result(
                "dependency_missing",
                "退下工具缺少 httpx 依赖。",
                message_focus="退下工具缺少运行依赖。请告诉用户这次没有真正触发退下。",
            )

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(self._api_url, json=body)
        except httpx.TimeoutException:
            return _failure_result(
                "timeout",
                "退下请求超时。",
                message_focus="退下请求超时了。请告诉用户稍后再试。",
            )
        except httpx.RequestError as exc:
            logger.warning("device_dismiss request failed: {}", exc)
            return _failure_result(
                "request_error",
                "无法连接到退下服务。",
                message_focus="这次没连上退下服务。请用轻松一点的语气告诉用户稍后再试。",
            )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return _failure_result(
                "http_error",
                f"退下服务返回 HTTP {exc.response.status_code}。",
                message_focus="退下服务返回错误。请告诉用户这次没弄成，让她稍后再试。",
            )

        try:
            result = response.json()
        except json.JSONDecodeError:
            return _failure_result(
                "invalid_response",
                "退下服务响应不是有效 JSON。",
                message_focus="退下服务返回内容异常。请告诉用户这次没有确认成功。",
            )

        code = int(result.get("code", -1))
        if code != 0:
            msg = str(result.get("msg") or "").strip()
            return _failure_result(
                "device_error",
                f"退下失败：{msg}" if msg else "退下失败。",
                message_focus="退下没有成功。请用朋友口吻告诉用户，不要暴露技术细节。",
            )

        return ToolResult(
            content=json.dumps(
                {
                    "ok": True,
                    "action": "dismiss_started",
                    "message_focus": (
                        "退下流程已启动，后端会播放告别语并退出房间。"
                        "请用自然简短的话道别，不要再说长篇或继续控制设备。"
                    ),
                },
                ensure_ascii=False,
            )
        )


def _coerce_device_id(value: int | str | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _failure_result(status: str, error: str, *, message_focus: str) -> ToolResult:
    payload = {
        "ok": False,
        "action": "error",
        "status": status,
        "error": error,
        "message_focus": message_focus,
    }
    return ToolResult(content=json.dumps(payload, ensure_ascii=False), ok=False)
