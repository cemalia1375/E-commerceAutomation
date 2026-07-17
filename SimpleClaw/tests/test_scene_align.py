"""Unit tests for Flowcut/services/scene_align.py — pure function, no I/O."""
import pytest
from Flowcut.services.scene_align import align_timestamps, detect_scene_cuts


# ── align_timestamps ──────────────────────────────────────────────────────────

def test_align_basic_snaps_to_nearest_cut():
    segments = [
        {"start_time": 0.0, "end_time": 4.2, "content": "A"},
        {"start_time": 4.2, "end_time": 8.1, "content": "B"},
        {"start_time": 8.1, "end_time": 11.7, "content": "C"},
    ]
    cuts = [0.0, 3.96, 7.92, 12.03]
    result = align_timestamps(segments, cuts)

    assert result[0]["start_time"] == 0.0
    assert result[0]["end_time"] == pytest.approx(3.96)
    assert result[1]["start_time"] == pytest.approx(3.96)
    assert result[1]["end_time"] == pytest.approx(7.92)
    assert result[2]["start_time"] == pytest.approx(7.92)
    assert result[2]["end_time"] == pytest.approx(12.03)


def test_align_first_segment_always_starts_at_zero():
    segments = [{"start_time": 0.5, "end_time": 4.0, "content": "A"}]
    cuts = [0.0, 4.1]
    result = align_timestamps(segments, cuts)
    assert result[0]["start_time"] == 0.0


def test_align_no_overlap_between_segments():
    segments = [
        {"start_time": 0.0, "end_time": 5.0, "content": "A"},
        {"start_time": 4.8, "end_time": 9.0, "content": "B"},
    ]
    cuts = [0.0, 5.1, 9.2]
    result = align_timestamps(segments, cuts)
    assert result[0]["end_time"] <= result[1]["start_time"]


def test_align_minimum_duration_enforced():
    """Segment shorter than 0.5s after alignment gets stretched to next cut."""
    segments = [
        {"start_time": 0.0, "end_time": 0.2, "content": "A"},
        {"start_time": 0.2, "end_time": 4.0, "content": "B"},
    ]
    cuts = [0.0, 3.96]
    result = align_timestamps(segments, cuts)
    assert result[0]["end_time"] - result[0]["start_time"] >= 0.5


def test_align_preserves_content():
    segments = [{"start_time": 0.0, "end_time": 3.0, "content": "hello"}]
    cuts = [0.0, 3.0]
    result = align_timestamps(segments, cuts)
    assert result[0]["content"] == "hello"


def test_align_empty_cuts_returns_original_times():
    segments = [{"start_time": 0.0, "end_time": 4.0, "content": "A"}]
    result = align_timestamps(segments, [])
    assert result[0]["start_time"] == 0.0
    assert result[0]["end_time"] == pytest.approx(4.0)


def test_align_window_no_match_keeps_original():
    """When no cut is within ±1s, keep the original timestamp."""
    segments = [{"start_time": 0.0, "end_time": 4.0, "content": "A"}]
    cuts = [0.0, 10.0]
    result = align_timestamps(segments, cuts)
    assert result[0]["end_time"] == pytest.approx(4.0)


# ── 口播段（copy 非空）：顺延到句末后的画面切点，不被切短 ──────────────────

def test_align_talk_segment_snaps_forward_not_nearest():
    """口播段 end 应顺延到 >= end 的切点（句子说完），而非吸附到更近但更早的切点。"""
    segments = [{"start_time": 0.0, "end_time": 2.0, "copy": "完整的一句话。", "category": "真人口播"}]
    cuts = [0.0, 1.8, 3.0]
    result = align_timestamps(segments, cuts)
    # 绝对最近会选 1.8（切掉句尾），口播段应顺延到 3.0
    assert result[0]["end_time"] == pytest.approx(3.0)


def test_align_silent_segment_snaps_nearest():
    """空镜段（copy 为空）维持绝对最近吸附（画面优先）。"""
    segments = [{"start_time": 0.0, "end_time": 2.0, "copy": "", "category": "产品展示"}]
    cuts = [0.0, 1.8, 3.0]
    result = align_timestamps(segments, cuts)
    assert result[0]["end_time"] == pytest.approx(1.8)


def test_align_talk_segment_no_forward_cut_keeps_semantic_end():
    """口播段往后窗口内无切点时，保留 Gemini 语义 end，不往前吸附切断口播。"""
    segments = [{"start_time": 0.0, "end_time": 2.0, "copy": "一句话。", "category": "真人口播"}]
    cuts = [0.0, 1.8]
    result = align_timestamps(segments, cuts)
    assert result[0]["end_time"] == pytest.approx(2.0)
