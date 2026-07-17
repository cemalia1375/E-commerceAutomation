"""Mojing API 端点的请求载荷标准化处理。

两个入口函数：
  resolve_agent_chat_context(payload)          — 用于 POST /agent/chat
  resolve_volcano_context(payload, headers)    — 用于 POST /v1/chat/completions
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

_IMAGE_LINE_RE = re.compile(r"^(?:\s*\[图片\]\s*)+(\S+)\s*$")
_INTERNAL_SESSION_PREFIXES = ("main:", "skin_diary:", "deep_report:")
_MAIN_SESSION_PREFIX = "main:"


def _dedupe_media(media: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for ref in media:
        value = str(ref or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _split_image_markers(message: str, media: list[str]) -> tuple[str, list[str]]:
    """把 admin UI 里显示用的 [图片] URL 行归一化到 media 字段。"""
    text_lines: list[str] = []
    merged_media = list(media)
    for line in str(message or "").splitlines():
        match = _IMAGE_LINE_RE.match(line)
        if match:
            merged_media.append(match.group(1))
            continue
        text_lines.append(line)
    return "\n".join(text_lines).strip(), _dedupe_media(merged_media)


def normalize_session_key(raw_session: Any, user_id: str) -> str:
    """Normalize a route/session id to the internal session namespace."""
    raw = str(raw_session or "").strip()
    if not raw:
        return f"main:{user_id}"
    if raw.startswith(_INTERNAL_SESSION_PREFIXES):
        return raw
    return f"main:{raw}"


def normalize_origin_session_key(raw_origin_session: Any, user_id: str, *, session_key: str = "") -> str:
    """Normalize the parent business session used by external tools.

    `session_key` routes the current turn and may point at a subagent lane.
    `origin_session_key` is always the main session external services should
    bind to, such as image analysis and deep report generation.
    """
    raw = str(raw_origin_session or "").strip()
    if raw:
        if raw.startswith(_MAIN_SESSION_PREFIX):
            return raw
        if raw.startswith(_INTERNAL_SESSION_PREFIXES):
            return f"main:{user_id}"
        return f"main:{raw}"

    current = str(session_key or "").strip()
    if current.startswith(_MAIN_SESSION_PREFIX):
        return current
    return f"main:{user_id}"


# ---------------------------------------------------------------------------
# /agent/chat
# ---------------------------------------------------------------------------

def resolve_agent_chat_context(payload: dict[str, Any]) -> dict[str, Any]:
    """将 /agent/chat 请求体标准化为统一的上下文字典。

    tenant_key = user_id（在魔镜模型中，用户即租户）
    session_key = main:{session_id}；未传 session_id 时为 "main:{user_id}"。
    已经带 main:/skin_diary:/deep_report: 前缀的内部 session_key 不重复拼接。
    """
    user_id = str(payload.get("user_id") or payload.get("tenant_key") or "__default__")
    user_context = payload.get("user_context") or {}
    raw_session = (
        payload.get("session_id")
        or payload.get("session_key")
        or payload.get("backend_session_id")
        or payload.get("backendSessionId")
        or user_context.get("session_id")
        or user_context.get("session_key")
        or user_context.get("backend_session_id")
        or user_context.get("backendSessionId")
    )
    session_key = normalize_session_key(raw_session, user_id)
    raw_origin_session = (
        payload.get("origin_session_id")
        or payload.get("originSessionId")
        or payload.get("origin_session_key")
        or payload.get("originSessionKey")
        or payload.get("backend_session_id")
        or payload.get("backendSessionId")
        or payload.get("parent_session_id")
        or payload.get("parentSessionId")
        or payload.get("parent_session_key")
        or payload.get("parentSessionKey")
        or user_context.get("origin_session_id")
        or user_context.get("originSessionId")
        or user_context.get("origin_session_key")
        or user_context.get("originSessionKey")
        or user_context.get("backend_session_id")
        or user_context.get("backendSessionId")
        or user_context.get("parent_session_id")
        or user_context.get("parentSessionId")
        or user_context.get("parent_session_key")
        or user_context.get("parentSessionKey")
    )
    origin_session_key = normalize_origin_session_key(raw_origin_session, user_id, session_key=session_key)

    media: list[str] = []
    # 兼容三种字段名（向后兼容，逐步统一）：
    #   payload["image"]        — 单条 URL（旧字段）
    #   payload["image_base64"] — 单条 base64（旧字段）
    #   payload["media"]        — URL 数组（admin 前端 / 通用客户端）
    if payload.get("image"):
        media.append(str(payload["image"]))
    if payload.get("image_base64"):
        media.append(f"data:image/jpeg;base64,{payload['image_base64']}")
    if isinstance(payload.get("media"), list):
        for item in payload["media"]:
            if isinstance(item, str) and item.strip():
                media.append(item.strip())

    message, media = _split_image_markers(str(payload.get("message") or ""), media)
    device_id = payload.get("device_id") or payload.get("deviceId") or user_context.get("device_id") or user_context.get("deviceId")
    device_code = payload.get("device_code") or payload.get("deviceCode") or user_context.get("device_code") or user_context.get("deviceCode")

    # 深度报告子 Agent 专用：前端可显式指定要解读的报告 ID（兼容 snake / camel）
    # 一次性归一化：兼容字面 "0"、空白字符串、None、缺省键。
    raw_report_id = payload.get("report_id") or payload.get("reportId") or ""
    report_id = str(raw_report_id).strip() or None

    return {
        "tenant_key":   user_id,
        "session_key":  session_key,
        "origin_session_key": origin_session_key,
        "message":      message,
        "message_id":   payload.get("message_id"),
        "media":        media,
        "device_id":    device_id,
        "device_code":  device_code,
        "prompt_surface": "app",
        "user_context": user_context,
        "report_id":    report_id,
        "stream":       payload.get("stream", True),
    }


# ---------------------------------------------------------------------------
# /v1/chat/completions
# ---------------------------------------------------------------------------

def resolve_volcano_context(
    payload: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    """将 /v1/chat/completions 请求标准化为统一的上下文字典。

    user_id 和 device_id 来自 `custom` 字段（JSON 字符串）。
    用户消息从 messages 数组中提取（取最后一条 user 轮次）。
    """
    # --- custom 字段（JSON 字符串）---
    custom_raw = payload.get("custom") or "{}"
    try:
        custom: dict = json.loads(custom_raw) if isinstance(custom_raw, str) else custom_raw
    except Exception:
        custom = {}

    user_id = str(custom.get("user_id") or "__default__")
    device_id = custom.get("device_id") or custom.get("deviceId")
    device_code = custom.get("device_code") or custom.get("deviceCode")
    capture_photo_enabled = _coerce_bool(
        custom.get("capture_photo_enabled", custom.get("capturePhotoEnabled", True)),
        default=True,
    )
    raw_session = (
        custom.get("session_id")
        or custom.get("session_key")
        or custom.get("backend_session_id")
        or custom.get("backendSessionId")
        or payload.get("session_id")
        or payload.get("session_key")
        or payload.get("backend_session_id")
        or payload.get("backendSessionId")
    )
    session_key = normalize_session_key(raw_session, user_id)
    raw_origin_session = (
        custom.get("origin_session_id")
        or custom.get("originSessionId")
        or custom.get("origin_session_key")
        or custom.get("originSessionKey")
        or custom.get("backend_session_id")
        or custom.get("backendSessionId")
        or custom.get("parent_session_id")
        or custom.get("parentSessionId")
        or custom.get("parent_session_key")
        or custom.get("parentSessionKey")
        or payload.get("origin_session_id")
        or payload.get("originSessionId")
        or payload.get("origin_session_key")
        or payload.get("originSessionKey")
        or payload.get("backend_session_id")
        or payload.get("backendSessionId")
        or payload.get("parent_session_id")
        or payload.get("parentSessionId")
        or payload.get("parent_session_key")
        or payload.get("parentSessionKey")
    )
    origin_session_key = normalize_origin_session_key(raw_origin_session, user_id, session_key=session_key)

    # --- 从 messages 数组中提取最后一条用户消息 ---
    message = ""
    media: list[str] = []
    for msg in reversed(payload.get("messages") or []):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            message = content.strip()
        elif isinstance(content, list):
            texts: list[str] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    texts.append(part.get("text") or "")
                elif part.get("type") == "image_url":
                    url = (part.get("image_url") or {}).get("url") or ""
                    if url:
                        media.append(url)
            message = " ".join(texts).strip()
        break

    message, media = _split_image_markers(message, media)
    if "【设备照片返回】" in message:
        capture_photo_enabled = False

    # --- X-Biz-Trace-Info 请求头（base64 编码的 JSON）---
    trace_info = None
    raw_trace = headers.get("x-biz-trace-info") or headers.get("X-Biz-Trace-Info")
    if raw_trace:
        try:
            trace_info = json.loads(base64.b64decode(raw_trace).decode())
        except Exception:
            pass

    return {
        "tenant_key":  user_id,
        "session_key": session_key,
        "origin_session_key": origin_session_key,
        "user_id":     user_id,
        "device_id":   device_id,
        "device_code": device_code,
        "capture_photo_enabled": capture_photo_enabled,
        "prompt_surface": "device",
        "message":     message,
        "media":       media,
        "trace_info":  trace_info,
        "model":       payload.get("model") or "",
    }


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default
