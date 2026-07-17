from __future__ import annotations

from simpleclaw.core.messages import AssistantMessage

from Mojing.runtime.photo_capture import build_capture_photo_problem_message


def test_capture_photo_problem_first_failure_asks_for_one_retry() -> None:
    reply = build_capture_photo_problem_message({"action": "photo_failed"})

    assert "重新对准" in reply
    assert "我重新拍" in reply
    assert "手机" not in reply


def test_capture_photo_problem_retry_failure_uses_phone_fallback() -> None:
    reply = build_capture_photo_problem_message(
        {"action": "photo_failed"},
        user_message="好，我重新对准了，再拍一次",
    )

    assert "手机" in reply
    assert "清晰面部照" in reply
    assert "小程序" in reply
    assert "重新对准" not in reply
    assert "我重新拍" not in reply


def test_capture_photo_problem_recent_failure_uses_phone_fallback() -> None:
    reply = build_capture_photo_problem_message(
        {"action": "photo_timeout", "timeout_s": 15},
        messages=[
            AssistantMessage("刚才这张没拍下来，图片没有成功返回。你重新对准一下，再跟我说一声，我重新拍。"),
        ],
    )

    assert "手机" in reply
    assert "清晰面部照" in reply
    assert "15 秒" not in reply
    assert "重新对准" not in reply


def test_capture_photo_problem_previous_user_no_photo_uses_phone_fallback() -> None:
    reply = build_capture_photo_problem_message(
        {"action": "photo_failed"},
        messages=[
            AssistantMessage("你重新对准一下，再跟我说一声，我重新拍。"),
            type("UserMessageStub", (), {"content": "你刚才没有拍照吧"})(),
        ],
    )

    assert "手机" in reply
    assert "清晰面部照" in reply
    assert "之前的照片" in reply
    assert "我重新拍" not in reply
