from __future__ import annotations

import pytest

from Flowcut.services.asr_timeline import (
    build_span_asr_timeline,
    correct_start_to_sentence,
    pick_asr_end_boundary,
    words_to_sentences,
)
from Flowcut.services.clip_planner import EpisodeRef


pytestmark = pytest.mark.unit


def test_words_to_sentences_uses_punctuation_and_pause():
    words = [
        {"text": "你", "start_time": 0, "end_time": 200},
        {"text": "好。", "start_time": 220, "end_time": 500},
        {"text": "等", "start_time": 1600, "end_time": 1800},
        {"text": "一下", "start_time": 1820, "end_time": 2200},
    ]

    sentences = words_to_sentences(words)

    assert sentences == [
        {"text": "你好。", "start_time": 0.0, "end_time": 0.5, "source": "asr"},
        {"text": "等一下", "start_time": 1.6, "end_time": 2.2, "source": "asr"},
    ]


def test_correct_start_to_sentence_backtracks_when_candidate_is_inside_sentence():
    sentences = [
        {"text": "老陈，你什么意思？", "start_time": 10.0, "end_time": 13.0},
    ]

    corrected, info = correct_start_to_sentence(
        candidate_start=11.5,
        current_start=11.0,
        content_start=0.0,
        sentences=sentences,
    )

    assert corrected == pytest.approx(10.0)
    assert info is not None
    assert info["sentence"]["text"] == "老陈，你什么意思？"


def test_correct_start_to_sentence_respects_content_start():
    sentences = [
        {"text": "片头后第一句", "start_time": 2.0, "end_time": 5.0},
    ]

    corrected, _info = correct_start_to_sentence(
        candidate_start=3.0,
        current_start=4.0,
        content_start=3.5,
        sentences=sentences,
    )

    assert corrected == pytest.approx(3.5)


def test_build_span_asr_timeline_maps_episode_local_times():
    eps = [
        EpisodeRef(asset_id=1, episode_no=3, oss_key="ep3.mp4", duration=20.0),
        EpisodeRef(asset_id=2, episode_no=4, oss_key="ep4.mp4", duration=30.0),
    ]
    sentences = {
        3: [
            {"text": "before", "start_time": 1.0, "end_time": 3.0},
            {"text": "start sentence", "start_time": 8.0, "end_time": 12.0},
        ],
        4: [
            {"text": "next episode", "start_time": 2.0, "end_time": 4.0},
        ],
    }

    timeline = build_span_asr_timeline(
        start_episode_no=3,
        start_local=7.0,
        episode_refs=eps,
        sentences_by_episode=sentences,
    )

    assert [s["text"] for s in timeline] == ["start sentence", "next episode"]
    assert timeline[0]["cum_end"] == pytest.approx(5.0)
    assert timeline[1]["cum_start"] == pytest.approx(15.0)


def test_pick_asr_end_boundary_prefers_sentence_nearest_ideal():
    timeline = [
        {"episode_no": 1, "local_end": 50.0, "cum_end": 50.0, "text": "a"},
        {"episode_no": 1, "local_end": 61.0, "cum_end": 61.0, "text": "b"},
        {"episode_no": 1, "local_end": 73.0, "cum_end": 73.0, "text": "c"},
    ]

    picked = pick_asr_end_boundary(timeline, window=(45.0, 75.0), ideal=60.0)

    assert picked is not None
    assert picked["text"] == "b"
