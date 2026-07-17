"""Device command tool for Mojing hardware control."""

from __future__ import annotations

import json
import logging
import uuid
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

from Mojing.runtime.photo_capture import PhotoCaptureCoordinator


ERROR_CODE_HINTS: dict[int, str] = {
    1_001_009_001: "deviceId 和 deviceCode 至少需要传一个，请检查设备参数",
    1_001_009_002: "不支持的指令，请换用其他指令",
    1_001_000_001: "设备不存在，请确认设备 ID 或编码是否正确",
    1_001_000_004: "设备离线，请告诉用户稍后再试",
    1_001_001_001: "设备服务暂时不可用（MQTT 未连接），请告诉用户稍后再试",
    1_015_002_001: "设备正在拍照中，请告诉用户等约 30 秒再试",
    1_001_001_002: "设备服务暂时不可用（MQTT 发布失败），请告诉用户稍后再试",
}

SUPPORTED_COMMANDS = frozenset(
    {
        "volume_up",
        "volume_down",
        "volume_set",
        "capture_photo",
        "light_color_temp",
        "light_brightness_up",
        "light_brightness_down",
        "light_brightness_set",
        "light_preset_mode",
        "notification",
    }
)

# 语义别名 → (标准 command, 默认 params)。模型若乱造 command，在此归一。
COMMAND_ALIASES: dict[str, tuple[str, dict[str, Any]]] = {
    "light_on": ("light_brightness_set", {"percentage": 80}),
    "turn_on_light": ("light_brightness_set", {"percentage": 80}),
    "open_light": ("light_brightness_set", {"percentage": 80}),
    "开灯": ("light_brightness_set", {"percentage": 80}),
    "light_off": ("light_brightness_set", {"percentage": 0}),
    "turn_off_light": ("light_brightness_set", {"percentage": 0}),
    "close_light": ("light_brightness_set", {"percentage": 0}),
    "关灯": ("light_brightness_set", {"percentage": 0}),
    "warm_light": ("light_color_temp", {"mode": "warm"}),
    "warm_tone": ("light_color_temp", {"mode": "warm"}),
    "暖色": ("light_color_temp", {"mode": "warm"}),
    "暖色调": ("light_color_temp", {"mode": "warm"}),
    "cool_light": ("light_color_temp", {"mode": "cool"}),
    "cool_tone": ("light_color_temp", {"mode": "cool"}),
    "冷色": ("light_color_temp", {"mode": "cool"}),
    "冷色调": ("light_color_temp", {"mode": "cool"}),
}

# 模型常错字段 → MQTT/Java 契约字段（见 doc/mqtt/MQTT硬件下发指令文档.md）
_PARAM_KEY_ALIASES: dict[str, dict[str, str]] = {
    "volume_set": {
        "volume": "percentage",
        "value": "percentage",
        "level": "percentage",
        "percent": "percentage",
    },
    "light_brightness_set": {
        "brightness": "percentage",
        "volume": "percentage",
        "value": "percentage",
        "level": "percentage",
        "percent": "percentage",
    },
    "light_color_temp": {
        "colorTempMode": "mode",
        "color_temp_mode": "mode",
        "color_temp": "mode",
        "tone": "mode",
        "temp": "mode",
    },
    "light_preset_mode": {
        "presetMode": "mode",
        "preset_mode": "mode",
        "preset": "mode",
    },
}

_COLOR_TEMP_MODES = frozenset({"cool", "warm", "mixed"})
_LIGHT_PRESET_MODES = frozenset(
    {
        "commute",
        "energetic",
        "dating",
        "streaming",
        "skincare",
        "banquet",
        "makeup_removal",
        "reading",
    }
)
_LIGHT_PRESET_MODE_ALIASES = {
    "通勤": "commute",
    "通勤光": "commute",
    "通勤模式": "commute",
    "commute_light": "commute",
    "元气": "energetic",
    "元气光": "energetic",
    "元气模式": "energetic",
    "energetic_light": "energetic",
    "约会": "dating",
    "约会光": "dating",
    "约会模式": "dating",
    "date": "dating",
    "date_light": "dating",
    "date_mode": "dating",
    "dating_light": "dating",
    "dating_mode": "dating",
    "直播": "streaming",
    "直播光": "streaming",
    "直播模式": "streaming",
    "live": "streaming",
    "live_light": "streaming",
    "livestream": "streaming",
    "stream": "streaming",
    "streaming_light": "streaming",
    "护肤": "skincare",
    "护肤光": "skincare",
    "护肤模式": "skincare",
    "skincare_light": "skincare",
    "晚宴": "banquet",
    "晚宴光": "banquet",
    "晚宴模式": "banquet",
    "晚艳": "banquet",
    "晚艳光": "banquet",
    "晚艳模式": "banquet",
    "evening": "banquet",
    "evening_glam": "banquet",
    "evening_glam_light": "banquet",
    "banquet_light": "banquet",
    "卸妆": "makeup_removal",
    "卸妆光": "makeup_removal",
    "卸妆模式": "makeup_removal",
    "makeup_removal_light": "makeup_removal",
    "makeup_removal_mode": "makeup_removal",
    "makeup_remove": "makeup_removal",
    "makeup_remove_light": "makeup_removal",
    "remove_makeup": "makeup_removal",
    "remove_makeup_light": "makeup_removal",
    "remove_makeup_mode": "makeup_removal",
    "阅读": "reading",
    "阅读光": "reading",
    "阅读模式": "reading",
    "reading_light": "reading",
    "reading_mode": "reading",
    "read_light": "reading",
    "reader_light": "reading",
}


class DeviceCommandTool(Tool):
    """向魔镜设备发送硬件控制指令。"""

    name = "device_command"
    description = (
        "向魔镜设备发送硬件控制指令（音量、灯光、拍照、通知等）。"
        "只提供 command 和可选 params；不要填写 deviceId/deviceCode。"
        "params 必须严格使用 MQTT 契约字段："
        "volume_set → {\"percentage\":0-100}；"
        "light_brightness_set → {\"percentage\":0-100}（0=关灯）；"
        "light_color_temp → {\"mode\":\"cool|warm|mixed\"}；"
        "light_preset_mode → {\"mode\":\"commute|energetic|dating|streaming|skincare|banquet|makeup_removal|reading\"}；"
        "用户要通勤光、元气光、约会光、直播光、护肤光、晚宴/晚艳光、卸妆光、阅读光时，"
        "优先调用 light_preset_mode，不要用其他灯光替代，也不要拆成亮度+色温；"
        "volume_up/down、light_brightness_up/down → {\"step\":1-100}；"
        "capture_photo → {} 或 {\"quality\":\"low|medium|high\"}。"
        "禁止用 volume/brightness 作为字段名。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "指令代码。标准值：volume_up、volume_down、volume_set、"
                    "light_brightness_up、light_brightness_down、light_brightness_set、"
                    "light_color_temp、light_preset_mode、capture_photo、notification。"
                    "场景灯光必须用 light_preset_mode：通勤=commute，元气=energetic，"
                    "约会=dating，直播=streaming，护肤=skincare，晚宴/晚艳=banquet，"
                    "卸妆=makeup_removal，阅读=reading。"
                    "开灯/关灯/暖色调/冷色调也可用语义别名 light_on、light_off、warm_tone、cool_tone。"
                ),
            },
            "params": {
                "type": "object",
                "description": (
                    "可选参数，字段名必须与后端 MQTT 一致。"
                    "volume_set: percentage；light_brightness_set: percentage；"
                    "light_color_temp: mode(cool/warm/mixed)；"
                    "light_preset_mode: mode(commute/energetic/dating/streaming/skincare/banquet/makeup_removal/reading)；"
                    "step 类: step。"
                ),
            },
        },
        "required": ["command"],
    }
    needs_followup = True
    tool_category = "sync_write"

    def __init__(
        self,
        *,
        api_url: str,
        timeout_s: float = 10.0,
        photo_capture_coordinator: PhotoCaptureCoordinator | None = None,
        photo_wait_timeout_s: float = 15.0,
    ) -> None:
        self._api_url = api_url.strip()
        self._timeout_s = max(1.0, float(timeout_s))
        self._photo_capture_coordinator = photo_capture_coordinator
        self._photo_wait_timeout_s = max(0.1, float(photo_wait_timeout_s))
        self._device_id: int | None = None
        self._device_code: str | None = None
        self._session_key = ""
        self._tenant_key = ""
        self._origin_session_key: str | None = None
        self._message_id: str | None = None
        self._capture_photo_enabled = True

    def set_context(
        self,
        *,
        tenant_key: str = "",
        device_id: int | str | None = None,
        device_code: str | None = None,
        session_key: str = "",
        origin_session_key: str | None = None,
        message_id: str | None = None,
        capture_photo_enabled: bool = True,
        **_: Any,
    ) -> None:
        self._device_id = _coerce_device_id(device_id)
        dc = str(device_code or "").strip()
        self._device_code = dc or None
        self._tenant_key = str(tenant_key or "").strip()
        self._session_key = str(session_key or "").strip()
        self._origin_session_key = str(origin_session_key or "").strip() or None
        self._message_id = message_id
        self._capture_photo_enabled = bool(capture_photo_enabled)

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        command = str(params.get("command") or "").strip()
        if not command:
            return ["command is required"]
        opt_params = params.get("params")
        if opt_params is not None and not isinstance(opt_params, dict):
            return ["params must be an object when provided"]
        normalized_command, normalized_params = normalize_device_command(command, opt_params)
        if normalized_command not in SUPPORTED_COMMANDS:
            return [f"unsupported command: {command}"]
        invalid_preset = _invalid_light_preset_mode(normalized_command, normalized_params)
        if invalid_preset:
            return [f"unsupported preset mode: {invalid_preset}"]
        return []

    async def execute(self, command: str = "", params: dict[str, Any] | None = None) -> ToolResult:
        command = str(command or "").strip()
        if not self._device_id and not self._device_code:
            return _failure_result(
                "missing_device",
                "缺少设备信息，没法确认要控制哪台魔镜设备。",
                message_focus="没有拿到设备信息。请自然告诉用户这次没能找到设备，让她稍后再试。",
            )

        normalized_command, normalized_params = normalize_device_command(command, params)
        if normalized_command not in SUPPORTED_COMMANDS:
            return _failure_result(
                "unsupported_command",
                f"不支持的指令: {command}",
                message_focus="这次设备指令类型不支持。请用朋友口吻告诉用户没弄成，可以换种说法再试。",
            )
        invalid_preset = _invalid_light_preset_mode(normalized_command, normalized_params)
        if invalid_preset:
            return _failure_result(
                "unsupported_preset_mode",
                f"不支持的预设灯光模式: {invalid_preset}。支持: commute, energetic, dating, streaming, skincare, banquet, makeup_removal, reading",
                message_focus="这次没有识别到支持的灯光预设，设备指令没有发出去。请自然告诉用户换一种说法再试。",
            )
        if normalized_command == "capture_photo" and not self._capture_photo_enabled:
            return _failure_result(
                "capture_photo_disabled",
                "当前内部回图轮不允许再次触发拍照。",
                message_focus="不要再次拍照。请只基于当前图片完成回应。",
            )

        body: dict[str, Any] = {"command": normalized_command}
        if self._device_id is not None:
            body["deviceId"] = self._device_id
        if self._device_code:
            body["deviceCode"] = self._device_code
        if normalized_params:
            body["params"] = normalized_params

        capture_request_id = ""
        if normalized_command == "capture_photo":
            capture_request_id = f"cap_{uuid.uuid4().hex}"
            body["source"] = "simpleclaw_tool"
            body["sessionKey"] = self._session_key
            body["tenantKey"] = self._tenant_key
            body["originSessionKey"] = self._origin_session_key
            body["captureRequestId"] = capture_request_id
            if self._photo_capture_coordinator is not None:
                await self._photo_capture_coordinator.register(
                    capture_request_id=capture_request_id,
                    tenant_key=self._tenant_key,
                    session_key=self._session_key,
                    origin_session_key=self._origin_session_key,
                    device_id=self._device_id,
                    device_code=self._device_code,
                    message_id=self._message_id,
                )

        logger.info(
            "device_command POST {} session_key={} message_id={} command={} raw={} deviceId={} deviceCode={} params={}",
            self._api_url,
            self._session_key,
            self._message_id or "",
            normalized_command,
            command,
            body.get("deviceId"),
            body.get("deviceCode"),
            normalized_params,
        )

        try:
            import httpx
        except ImportError:
            return _failure_result(
                "dependency_missing",
                "设备控制工具缺少 httpx 依赖。",
                message_focus="设备控制工具缺少运行依赖。请告诉用户这次设备指令没有真正发出去。",
            )

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(self._api_url, json=body)
        except httpx.TimeoutException:
            return _failure_result(
                "timeout",
                "设备控制服务请求超时。",
                message_focus="设备指令超时了。请告诉用户设备可能没及时响应，可以稍后再试。",
            )
        except httpx.RequestError as exc:
            logger.warning("device_command request failed: {}", exc)
            return _failure_result(
                "request_error",
                "无法连接到设备控制服务。",
                message_focus="这次没连上设备控制服务。请用轻松一点的语气告诉用户稍后再试。",
            )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return _failure_result(
                "http_error",
                f"设备控制服务返回 HTTP {exc.response.status_code}。",
                message_focus="设备控制服务返回错误。请告诉用户这次指令没有成功，让她稍后再试。",
            )

        try:
            result = response.json()
        except json.JSONDecodeError:
            return _failure_result(
                "invalid_response",
                "设备控制服务响应不是有效 JSON。",
                message_focus="设备控制服务返回内容异常。请告诉用户这次没有确认执行成功。",
            )

        code = int(result.get("code", -1))
        if code == 0:
            if normalized_command == "capture_photo":
                data = result.get("data") if isinstance(result.get("data"), dict) else {}
                photo_id = str(data.get("photoId") or data.get("photo_id") or "").strip()
                if self._photo_capture_coordinator is not None:
                    await self._photo_capture_coordinator.attach_photo_id(capture_request_id, photo_id)
                    photo_payload = await self._photo_capture_coordinator.wait_for_photo(
                        capture_request_id,
                        timeout_s=self._photo_wait_timeout_s,
                    )
                else:
                    photo_payload = None

                if photo_payload and photo_payload.get("status") == "failed":
                    reason = str(photo_payload.get("reason") or photo_payload.get("error") or "图片没有成功返回").strip()
                    return ToolResult(
                        content=json.dumps(
                            {
                                "ok": True,
                                "action": "photo_failed",
                                "status": "failed",
                                "command": normalized_command,
                                "params": normalized_params,
                                "captureRequestId": capture_request_id,
                                "photoId": photo_payload.get("photoId") or photo_id,
                                "reason": reason,
                                "message_focus": (
                                    "硬件已经明确返回拍照失败。请自然告诉用户这张没拍下来，"
                                    "需要重新对准后再拍一次；不要再次调用 capture_photo。"
                                ),
                            },
                            ensure_ascii=False,
                        )
                    )

                if photo_payload and photo_payload.get("photoUrl"):
                    return ToolResult(
                        content=json.dumps(
                            {
                                "ok": True,
                                "action": "photo_ready",
                                "command": normalized_command,
                                "params": normalized_params,
                                "captureRequestId": capture_request_id,
                                "photoId": photo_payload.get("photoId") or photo_id,
                                "photoUrl": photo_payload.get("photoUrl"),
                                "cleanPhotoUrl": photo_payload.get("cleanPhotoUrl"),
                                "message_focus": (
                                    "照片已经返回。不要再次调用 capture_photo；"
                                    "交由业务层立刻开启带 image_url 的内部视觉回复轮。"
                                ),
                            },
                            ensure_ascii=False,
                        )
                    )

                return ToolResult(
                    content=json.dumps(
                        {
                            "ok": True,
                            "action": "photo_timeout",
                            "status": "timeout",
                            "command": normalized_command,
                            "params": normalized_params,
                            "captureRequestId": capture_request_id,
                            "photoId": photo_id or None,
                            "timeout_s": self._photo_wait_timeout_s,
                            "message_focus": (
                                "拍照动作已触发，但图片没有在等待窗口内返回。"
                                "请自然告诉用户这张图片暂时没回来，需要重新拍照；"
                                "不要再次调用 capture_photo。"
                            ),
                        },
                        ensure_ascii=False,
                    )
                )
            else:
                message_focus = "设备指令已经执行成功。请用自然简短的话告诉用户已经弄好了。"
                action = "executed"
            return ToolResult(
                content=json.dumps(
                    {
                        "ok": True,
                        "action": action,
                        "command": normalized_command,
                        "params": normalized_params,
                        "message_focus": message_focus,
                    },
                    ensure_ascii=False,
                )
            )

        hint = ERROR_CODE_HINTS.get(code, f"未知错误（code={code}）")
        msg = str(result.get("msg") or "").strip()
        detail = f"{hint}，详情：{msg}" if msg else hint
        return _failure_result(
            "device_error",
            detail,
            code=code,
            message_focus=f"设备指令没有执行成功：{hint}。请用朋友口吻告诉用户，不要暴露技术细节。",
        )


def normalize_device_command(
    command: str,
    params: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """归一 command/params，对齐 Java DeviceCommandDefaultParams + MQTT 契约。"""
    raw_command = str(command or "").strip()
    merged: dict[str, Any] = dict(params or {})

    alias = COMMAND_ALIASES.get(raw_command)
    if alias is not None:
        raw_command, alias_defaults = alias
        for key, value in alias_defaults.items():
            merged.setdefault(key, value)

    key_aliases = _PARAM_KEY_ALIASES.get(raw_command, {})
    if key_aliases:
        renamed: dict[str, Any] = {}
        for key, value in merged.items():
            canonical = key_aliases.get(str(key), str(key))
            if canonical not in renamed:
                renamed[canonical] = value
        merged = renamed

    if raw_command == "light_color_temp" and "mode" in merged:
        merged["mode"] = _normalize_color_temp_mode(merged["mode"])
    if raw_command == "light_preset_mode" and "mode" in merged:
        merged["mode"] = _normalize_preset_mode(merged["mode"])

    merged = _coerce_numeric_fields(raw_command, merged)
    merged = _apply_command_defaults(raw_command, merged)
    return raw_command, merged


def _normalize_color_temp_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "warm": "warm",
        "暖": "warm",
        "暖色": "warm",
        "暖色调": "warm",
        "warm_tone": "warm",
        "cool": "cool",
        "冷": "cool",
        "冷色": "cool",
        "冷色调": "cool",
        "cold": "cool",
        "cool_tone": "cool",
        "mixed": "mixed",
        "混合": "mixed",
    }
    normalized = mapping.get(text, text)
    if normalized not in _COLOR_TEMP_MODES:
        return "cool"
    return normalized


def _normalize_preset_mode(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in _LIGHT_PRESET_MODES:
        return text
    return _LIGHT_PRESET_MODE_ALIASES.get(text, text)


def _invalid_light_preset_mode(command: str, params: dict[str, Any]) -> str:
    if command != "light_preset_mode":
        return ""
    mode = str(params.get("mode") or "").strip()
    if mode in _LIGHT_PRESET_MODES:
        return ""
    return mode or "<empty>"


def _coerce_numeric_fields(command: str, params: dict[str, Any]) -> dict[str, Any]:
    out = dict(params)
    if command in {"volume_set", "light_brightness_set"} and "percentage" in out:
        out["percentage"] = _clamp_int(out["percentage"], 0, 100)
    if command in {"volume_up", "volume_down", "light_brightness_up", "light_brightness_down"} and "step" in out:
        out["step"] = _clamp_int(out["step"], 1, 100)
    return out


def _apply_command_defaults(command: str, params: dict[str, Any]) -> dict[str, Any]:
    out = dict(params)
    if command in {"volume_up", "volume_down", "light_brightness_up", "light_brightness_down"}:
        out.setdefault("step", 5)
    if command in {"volume_set", "light_brightness_set"}:
        out.setdefault("percentage", 50)
    if command == "capture_photo":
        out.setdefault("quality", "medium")
    if command == "light_color_temp":
        out.setdefault("mode", "cool")
    if command == "light_preset_mode":
        out.setdefault("mode", "commute")
    if command == "notification":
        out.setdefault("_source", "agent")
    return out


def _clamp_int(value: Any, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))


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
