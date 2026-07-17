"""Device status query tool for Mojing hardware."""

from __future__ import annotations

import json
import logging
from typing import Any

try:
    from loguru import logger
except ImportError:  # pragma: no cover - local fallback for minimal test environments
    class _FallbackLogger:
        _logger = logging.getLogger(__name__)

        def info(self, message: str, *args: Any) -> None:
            self._logger.info(message.format(*args))

        def warning(self, message: str, *args: Any) -> None:
            self._logger.warning(message.format(*args))

    logger = _FallbackLogger()

from simpleclaw.tools.base import Tool, ToolResult


# 字段别名 → 标准字段（与后端 AgentDeviceStatusServiceImpl 对齐）
_FIELD_ALIASES: dict[str, str] = {
    "battery": "battery_level",
    "电量": "battery_level",
    "signal": "signal_strength",
    "信号": "signal_strength",
    "version": "firmware_version",
    "版本": "firmware_version",
    "固件版本": "firmware_version",
    "online": "status",
    "在线": "status",
    "离线": "status",
    "mqtt": "mqtt_status",
    "name": "device_name",
    "设备名": "device_name",
    "volume": "current_volume",
    "音量": "current_volume",
    "brightness": "current_brightness",
    "light": "current_brightness",
    "亮度": "current_brightness",
    "灯光": "current_brightness",
    "灯光亮度": "current_brightness",
    "color_temp": "color_temp_percentage",
    "色温": "color_temp_percentage",
    "色温百分比": "color_temp_percentage",
}

_VALID_FIELDS = frozenset({
    "battery_level", "signal_strength", "status", "firmware_version",
    "mqtt_status", "last_connect_time", "last_mqtt_message_time", "device_name",
    "current_volume", "current_brightness", "color_temp_percentage",
})


class DeviceStatusTool(Tool):
    """查询魔镜设备的当前状态（电量、信号、音量、灯光、固件版本等）。

    直接读取后端数据库中设备最新上报的状态，不经过 MQTT 下发指令。
    """

    name = "device_status"
    description = (
        "查询魔镜设备的当前状态（电量、信号强度、在线状态、固件版本、MQTT状态、音量、灯光亮度、色温等）。"
        "不传参数时返回所有状态；传 field 可查询单个状态。"
        "这是只读工具，不会触发硬件动作。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "field": {
                "type": "string",
                "description": (
                    "可选。要查询的单个状态字段。"
                    "支持：battery_level（电量）、signal_strength（信号）、"
                    "status（在线状态）、firmware_version（固件版本）、"
                    "mqtt_status（MQTT连接状态）、device_name（设备名称）、"
                    "current_volume（音量）、current_brightness（灯光亮度）、"
                    "color_temp_percentage（色温百分比）。"
                    "不传则返回所有状态。"
                ),
            },
        },
        "required": [],
    }
    needs_followup = True
    tool_category = "sync_read"

    def __init__(self, *, api_url: str, timeout_s: float = 10.0) -> None:
        self._api_url = api_url.strip()
        self._timeout_s = max(1.0, float(timeout_s))
        self._device_id: int | None = None
        self._device_code: str | None = None

    def set_context(
        self,
        *,
        device_id: int | str | None = None,
        device_code: str | None = None,
        **_: Any,
    ) -> None:
        self._device_id = _coerce_device_id(device_id)
        dc = str(device_code or "").strip()
        self._device_code = dc or None

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        field = str(params.get("field") or "").strip()
        if field:
            normalized = _normalize_field(field)
            if normalized not in _VALID_FIELDS:
                return [f"unsupported field: {field}"]
        return []

    async def execute(self, field: str = "") -> ToolResult:
        if not self._device_id and not self._device_code:
            return _failure_result(
                "missing_device",
                "缺少设备信息，没法确认要查询哪台魔镜设备的状态。",
                message_focus="没有拿到设备信息。请自然告诉用户这次没能找到设备，让她稍后再试。",
            )

        body: dict[str, Any] = {}
        if self._device_id is not None:
            body["deviceId"] = self._device_id
        if self._device_code:
            body["deviceCode"] = self._device_code

        normalized_field = _normalize_field(field)
        if normalized_field:
            body["field"] = normalized_field

        logger.info(
            "device_status POST {} field={} deviceId={} deviceCode={!r}",
            self._api_url,
            normalized_field or "(all)",
            body.get("deviceId"),
            body.get("deviceCode"),
        )

        try:
            import httpx
        except ImportError:
            return _failure_result(
                "dependency_missing",
                "设备状态查询工具缺少 httpx 依赖。",
                message_focus="设备状态查询工具缺少运行依赖。请告诉用户这次没查成。",
            )

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(self._api_url, json=body)
        except httpx.TimeoutException:
            return _failure_result(
                "timeout",
                "设备状态查询请求超时。",
                message_focus="查询设备状态超时了。请告诉用户稍后再试。",
            )
        except httpx.RequestError as exc:
            logger.warning("device_status request failed: {}", exc)
            return _failure_result(
                "request_error",
                "无法连接到设备状态查询服务。",
                message_focus="这次没连上设备状态查询服务。请用轻松一点的语气告诉用户稍后再试。",
            )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return _failure_result(
                "http_error",
                f"设备状态查询服务返回 HTTP {exc.response.status_code}。",
                message_focus="设备状态查询服务返回错误。请告诉用户这次没查成，让她稍后再试。",
            )

        try:
            result = response.json()
        except json.JSONDecodeError:
            return _failure_result(
                "invalid_response",
                "设备状态查询服务响应不是有效 JSON。",
                message_focus="设备状态查询服务返回内容异常。请告诉用户这次没有确认查询成功。",
            )

        code = int(result.get("code", -1))
        if code != 0:
            msg = str(result.get("msg") or "").strip()
            return _failure_result(
                "device_error",
                f"查询失败：{msg}" if msg else "查询设备状态失败。",
                message_focus="设备状态查询没有成功。请用朋友口吻告诉用户，不要暴露技术细节。",
            )

        data = result.get("data") or {}
        all_fields = data.get("allFields") or {}
        field_value = data.get("fieldValue")

        # 构建 user_visible_summary
        if normalized_field:
            summary = _single_field_summary(normalized_field, field_value)
        else:
            summary = _all_fields_summary(all_fields)

        return ToolResult(
            content=json.dumps(
                {
                    "ok": True,
                    "action": "queried",
                    "field": normalized_field or None,
                    "field_value": field_value if normalized_field else None,
                    "all_fields": all_fields if not normalized_field else None,
                    "user_visible_summary": summary,
                    "message_focus": (
                        "状态查询成功。请用自然简短的话告诉用户结果，"
                        "不要暴露内部字段名或技术细节。"
                    ),
                },
                ensure_ascii=False,
                default=str,
            )
        )


def _normalize_field(field: str) -> str:
    raw = str(field or "").strip().lower()
    return _FIELD_ALIASES.get(raw, raw)


def _single_field_summary(field: str, value: Any) -> str:
    if value is None:
        return "这个状态暂时还没有数据。"
    if field == "battery_level":
        return f"当前电量 {value}%。"
    if field == "signal_strength":
        return f"当前信号强度 {value}%。"
    if field == "status":
        return "设备当前在线。" if value == 1 else "设备当前离线。"
    if field == "mqtt_status":
        return "MQTT 连接正常。" if value == 1 else "MQTT 连接异常。"
    if field == "firmware_version":
        return f"固件版本是 {value}。"
    if field == "device_name":
        return f"设备名称是 {value}。"
    if field == "current_volume":
        return f"当前音量 {value}%。"
    if field == "current_brightness":
        return f"当前灯光亮度 {value}%。"
    if field == "color_temp_percentage":
        return f"当前色温 {value}%。"
    return f"{field} 是 {value}。"


def _all_fields_summary(all_fields: dict[str, Any]) -> str:
    parts: list[str] = []
    battery = all_fields.get("battery_level")
    signal = all_fields.get("signal_strength")
    status = all_fields.get("status")
    version = all_fields.get("firmware_version")
    mqtt = all_fields.get("mqtt_status")
    volume = all_fields.get("current_volume")
    brightness = all_fields.get("current_brightness")
    color_temp = all_fields.get("color_temp_percentage")

    if battery is not None:
        parts.append(f"电量 {battery}%")
    if signal is not None:
        parts.append(f"信号 {signal}%")
    if volume is not None:
        parts.append(f"音量 {volume}%")
    if brightness is not None:
        parts.append(f"灯光亮度 {brightness}%")
    if color_temp is not None:
        parts.append(f"色温 {color_temp}%")
    if status is not None:
        parts.append("在线" if status == 1 else "离线")
    if mqtt is not None:
        parts.append("MQTT正常" if mqtt == 1 else "MQTT异常")
    if version:
        parts.append(f"固件 {version}")

    if not parts:
        return "暂时还没有设备状态数据。"
    return "设备状态：" + "，".join(parts) + "。"


def _coerce_device_id(value: int | str | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _failure_result(
    status: str,
    error: str,
    *,
    message_focus: str,
    code: int | None = None,
) -> ToolResult:
    payload: dict[str, Any] = {
        "ok": False,
        "action": "error",
        "status": status,
        "error": error,
        "message_focus": message_focus,
    }
    if code is not None:
        payload["code"] = code
    return ToolResult(content=json.dumps(payload, ensure_ascii=False), ok=False)
