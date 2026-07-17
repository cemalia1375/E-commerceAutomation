"""Runtime coordination for device capture_photo tool results."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from Mojing.storage.image_repo import normalize_image_ref

_FIRST_PHOTO_FAILED_MESSAGE = "刚才这张没拍下来，图片没有成功返回。你重新对准一下，再跟我说一声，我重新拍。"
_SECOND_PHOTO_FAILED_MESSAGE = (
    "这次镜子还是没顺利拿到照片，我们先别折腾镜子重拍了。"
    "你先用手机拍一张清晰面部照，在小程序里发给我，我继续帮你看；"
    "没有新的有效照片前，我不会用之前的照片来判断你现在的状态。"
)

_RETRY_PHOTO_USER_MARKERS = (
    "重新对准",
    "再拍",
    "再来一张",
    "重试",
    "再试",
    "重新拍",
    "对准了",
)
_NO_PHOTO_USER_MARKERS = (
    "没拍到",
    "没有拍到",
    "没有拍照",
    "没拍",
    "没拿到照片",
    "还是没拍到",
    "用的是前面的照片",
    "前面的照片",
)
_RECENT_PHOTO_FAILURE_MARKERS = (
    "刚才这张没拍下来",
    "图片没有成功返回",
    "还没收到图片",
    "重新对准一下",
    "我重新拍",
    "镜子还是没顺利拿到照片",
    "没有新的有效照片",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def build_capture_photo_problem_message(
    photo_result: dict[str, Any],
    *,
    user_message: str = "",
    messages: Sequence[Any] | None = None,
) -> str:
    """Return the user-visible reply for capture_photo failures."""
    if _is_retry_photo_failure_context(user_message=user_message, messages=messages):
        return _SECOND_PHOTO_FAILED_MESSAGE

    action = str(photo_result.get("action") or "").strip()
    timeout_s = photo_result.get("timeout_s")
    if action in {"photo_timeout", "photo_pending"}:
        try:
            timeout_text = str(int(float(timeout_s)))
        except (TypeError, ValueError):
            timeout_text = "15"
        return f"我这边等了 {timeout_text} 秒还没收到图片，先不硬等了。你重新对准一下，再跟我说一声，我重新拍。"
    return _FIRST_PHOTO_FAILED_MESSAGE


def _is_retry_photo_failure_context(
    *,
    user_message: str = "",
    messages: Sequence[Any] | None = None,
) -> bool:
    current_text = str(user_message or "")
    if _contains_any(current_text, _RETRY_PHOTO_USER_MARKERS) or _contains_any(current_text, _NO_PHOTO_USER_MARKERS):
        return True

    for message in list(messages or [])[-10:]:
        if type(message).__name__ == "ToolResultMessage":
            continue
        text = _message_text(message)
        if _contains_any(text, _RECENT_PHOTO_FAILURE_MARKERS) or _contains_any(text, _NO_PHOTO_USER_MARKERS):
            return True
    return False


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return str(content or "")


def _contains_any(text: str, markers: Sequence[str]) -> bool:
    return any(marker in text for marker in markers)


@dataclass(slots=True)
class PendingPhotoCapture:
    capture_request_id: str
    tenant_key: str
    session_key: str
    origin_session_key: str | None = None
    device_id: int | str | None = None
    device_code: str | None = None
    message_id: str | None = None
    created_at_ms: int = field(default_factory=_now_ms)
    status: str = "pending"
    photo_id: str | None = None
    photo_url: str | None = None
    clean_photo_url: str | None = None
    error: str | None = None
    waiter_active: bool = False
    waiter_done: bool = False
    delivered_to_waiter: bool = False
    auto_continuation_sent: bool = False
    event: asyncio.Event = field(default_factory=asyncio.Event)

    def photo_payload(self) -> dict[str, Any]:
        return {
            "captureRequestId": self.capture_request_id,
            "status": self.status,
            "photoId": self.photo_id,
            "photoUrl": self.photo_url,
            "cleanPhotoUrl": self.clean_photo_url,
            "tenantKey": self.tenant_key,
            "sessionKey": self.session_key,
            "originSessionKey": self.origin_session_key,
            "deviceId": self.device_id,
            "deviceCode": self.device_code,
            "createdAtMs": self.created_at_ms,
        }

    def failure_payload(self) -> dict[str, Any]:
        return {
            "captureRequestId": self.capture_request_id,
            "status": "failed",
            "photoId": self.photo_id,
            "reason": self.error,
            "error": self.error,
            "tenantKey": self.tenant_key,
            "sessionKey": self.session_key,
            "originSessionKey": self.origin_session_key,
            "deviceId": self.device_id,
            "deviceCode": self.device_code,
            "createdAtMs": self.created_at_ms,
        }


class PhotoCaptureCoordinator:
    """Coordinates the short tool wait and late photo continuation decision."""

    def __init__(self) -> None:
        self._captures_by_request: dict[str, PendingPhotoCapture] = {}
        self._request_by_photo_id: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        *,
        capture_request_id: str,
        tenant_key: str,
        session_key: str,
        origin_session_key: str | None = None,
        device_id: int | str | None = None,
        device_code: str | None = None,
        message_id: str | None = None,
    ) -> PendingPhotoCapture:
        capture = PendingPhotoCapture(
            capture_request_id=capture_request_id,
            tenant_key=tenant_key,
            session_key=session_key,
            origin_session_key=origin_session_key,
            device_id=device_id,
            device_code=device_code,
            message_id=message_id,
        )
        async with self._lock:
            self._captures_by_request[capture_request_id] = capture
        return capture

    async def attach_photo_id(self, capture_request_id: str, photo_id: str | None) -> None:
        photo_id = str(photo_id or "").strip()
        if not photo_id:
            return
        async with self._lock:
            capture = self._captures_by_request.get(capture_request_id)
            if capture is None:
                return
            capture.photo_id = photo_id
            self._request_by_photo_id[photo_id] = capture_request_id

    async def wait_for_photo(self, capture_request_id: str, timeout_s: float) -> dict[str, Any] | None:
        async with self._lock:
            capture = self._captures_by_request.get(capture_request_id)
            if capture is None:
                return None
            capture.waiter_active = True
            event = capture.event

        try:
            await asyncio.wait_for(event.wait(), timeout=max(0.1, float(timeout_s)))
        except asyncio.TimeoutError:
            async with self._lock:
                capture = self._captures_by_request.get(capture_request_id)
                if capture is not None:
                    capture.waiter_active = False
                    capture.waiter_done = True
            return None

        async with self._lock:
            capture = self._captures_by_request.get(capture_request_id)
            if capture is None:
                return None
            capture.waiter_active = False
            capture.waiter_done = True
            if capture.status == "failed":
                capture.delivered_to_waiter = True
                return capture.failure_payload()
            if capture.photo_url:
                capture.delivered_to_waiter = True
                return capture.photo_payload()
            return None

    async def resolve_photo(
        self,
        *,
        capture_request_id: str | None = None,
        photo_id: str | None = None,
        photo_url: str | None = None,
        clean_photo_url: str | None = None,
    ) -> dict[str, Any]:
        request_id = str(capture_request_id or "").strip()
        photo_id = str(photo_id or "").strip()
        photo_url = normalize_image_ref(photo_url)
        clean_photo_url = normalize_image_ref(clean_photo_url) or photo_url

        async with self._lock:
            if not request_id and photo_id:
                request_id = self._request_by_photo_id.get(photo_id, "")
            capture = self._captures_by_request.get(request_id)
            if capture is None:
                return {
                    "ok": True,
                    "action": "recorded_only",
                    "reason": "pending_capture_not_found",
                    "captureRequestId": request_id or None,
                    "photoId": photo_id or None,
                }
            if photo_id:
                capture.photo_id = photo_id
                self._request_by_photo_id[photo_id] = capture.capture_request_id
            capture.status = "ready"
            capture.photo_url = photo_url or capture.photo_url
            capture.clean_photo_url = clean_photo_url or capture.clean_photo_url
            if capture.photo_url:
                capture.event.set()
            if capture.waiter_active:
                return {
                    "ok": True,
                    "action": "resolved_waiter",
                    **capture.photo_payload(),
                }
            if capture.waiter_done and not capture.delivered_to_waiter:
                return {
                    "ok": True,
                    "action": "recorded_only",
                    "reason": "waiter_timeout",
                    **capture.photo_payload(),
                }
            return {
                "ok": True,
                "action": "late_photo_ready",
                **capture.photo_payload(),
            }

    async def resolve_failure(
        self,
        *,
        capture_request_id: str | None = None,
        photo_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        request_id = str(capture_request_id or "").strip()
        photo_id = str(photo_id or "").strip()
        reason = str(reason or "").strip() or "photo_capture_failed"

        async with self._lock:
            if not request_id and photo_id:
                request_id = self._request_by_photo_id.get(photo_id, "")
            capture = self._captures_by_request.get(request_id)
            if capture is None:
                return {
                    "ok": True,
                    "action": "recorded_only",
                    "reason": "pending_capture_not_found",
                    "captureRequestId": request_id or None,
                    "photoId": photo_id or None,
                }
            if photo_id:
                capture.photo_id = photo_id
                self._request_by_photo_id[photo_id] = capture.capture_request_id
            capture.status = "failed"
            capture.error = reason
            capture.event.set()
            if capture.waiter_active:
                return {
                    "ok": True,
                    "action": "resolved_waiter",
                    **capture.failure_payload(),
                }
            if capture.waiter_done and not capture.delivered_to_waiter:
                return {
                    "ok": True,
                    "action": "recorded_only",
                    **capture.failure_payload(),
                    "reason": "waiter_timeout",
                }
            return {
                "ok": True,
                "action": "late_photo_failed",
                **capture.failure_payload(),
            }

    async def mark_auto_continuation_sent(self, capture_request_id: str) -> None:
        async with self._lock:
            capture = self._captures_by_request.get(capture_request_id)
            if capture is not None:
                capture.auto_continuation_sent = True

    async def get(self, capture_request_id: str) -> PendingPhotoCapture | None:
        async with self._lock:
            return self._captures_by_request.get(capture_request_id)
