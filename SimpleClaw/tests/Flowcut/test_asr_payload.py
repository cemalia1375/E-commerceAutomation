"""ASR config payload 单测 —— 防止 show_utterances 开关再次回归。"""
import json
import pytest

from Flowcut.runtime.executors import _build_asr_request_payload


@pytest.mark.unit
def test_asr_payload_enables_show_utterances():
    raw = _build_asr_request_payload()
    obj = json.loads(raw)
    assert obj["request"]["show_utterances"] is True, (
        "show_utterances 必须开启，否则 ASR 不返回词级时间戳，拆镜段 copy 字段会为空"
    )


@pytest.mark.unit
def test_asr_payload_keeps_punc_and_itn():
    obj = json.loads(_build_asr_request_payload())
    assert obj["request"]["enable_punc"] is True
    assert obj["request"]["enable_itn"] is True


@pytest.mark.unit
def test_asr_payload_audio_format_unchanged():
    obj = json.loads(_build_asr_request_payload())
    audio = obj["audio"]
    assert audio["format"] == "pcm"
    assert audio["rate"] == 16000
    assert audio["channel"] == 1
