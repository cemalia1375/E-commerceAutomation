import pytest

from Flowcut.services.clip_planner import (
    ClipEntry,
    ClipPlan,
    EndBoundary,
    EpisodeRef,
    StartCandidate,
    TimedSegment,
    build_clip_plan,
    build_timeline,
    expand_start_with_context,
    match_drama_episodes,
    pick_end_boundary,
    resolve_real_end,
    score_start_candidate,
    select_start_candidates,
    validate_start_candidate,
)


@pytest.mark.unit
def test_match_drama_episodes_substring_and_sort():
    # 库里剧名带「被」前缀，LLM 传入不带 → 子串命中；按 episode_no 排序
    rows = [
        {"drama_name": "被儿媳逼相亲，我成了设备维修天花板", "episode_no": 3, "name": "第3集.mp4"},
        {"drama_name": "被儿媳逼相亲，我成了设备维修天花板", "episode_no": 1, "name": "第1集.mp4"},
        {"drama_name": "别的剧", "episode_no": 1, "name": "x.mp4"},
    ]
    out = match_drama_episodes(rows, "儿媳逼相亲，我成了设备维修天花板")
    assert [r["episode_no"] for r in out] == [1, 3]


@pytest.mark.unit
def test_expand_start_with_context_never_goes_negative():
    expanded, log = expand_start_with_context(0.0, 0.0, pre_roll_s=3.0)

    assert expanded == 0.0
    assert "clamp" in log


@pytest.mark.unit
def test_match_drama_episodes_no_match_returns_empty():
    rows = [{"drama_name": "甲剧", "episode_no": 1, "name": "x"}]
    assert match_drama_episodes(rows, "乙剧") == []
    assert match_drama_episodes(rows, "") == []


def _seg(start, end, copy="", hook=0.0, use="context_only", ctx=0.0, risk=0.0):
    return {
        "start_time": start, "end_time": end, "copy": copy,
        "hook_strength": hook, "candidate_use": use,
        "context_dependency": ctx, "continuity_risk": risk,
    }


# offsets: 每集在「前3集拼接视频」里的全局起始秒；durations: 每集真实时长
OFFSETS = [(1, 0.0), (2, 20.0), (3, 41.0)]
DURATIONS = {1: 20.0, 2: 21.0, 3: 19.0}


@pytest.mark.unit
def test_validate_rejects_empty_opening_even_when_later_dialogue_exists():
    segs = [
        {"start_time": 10.0, "end_time": 13.0, "visual": "城市远景空镜，环境过渡", "copy": ""},
        {"start_time": 13.0, "end_time": 17.0, "visual": "两人在客厅争执", "copy": "你为什么骗我？"},
        {"start_time": 17.0, "end_time": 21.0, "visual": "女人质问男人", "copy": "我已经知道真相了。"},
    ]

    validation = validate_start_candidate(segs[0], segs, content_start=0.0)
    quality = score_start_candidate(segs[0], segs, content_start=0.0)

    assert validation.is_valid is False
    assert "empty_opening" in quality.reject_reasons


@pytest.mark.unit
def test_validate_allows_strong_visual_hook_without_dialogue():
    segs = [
        {"start_time": 20.0, "end_time": 23.0, "visual": "男人突然下跪，女人崩溃痛哭", "copy": ""},
        {"start_time": 23.0, "end_time": 27.0, "visual": "众人震惊围观", "copy": "你这是干什么？"},
    ]

    validation = validate_start_candidate(segs[0], segs, content_start=0.0)
    quality = score_start_candidate(segs[0], segs, content_start=0.0)

    assert validation.is_valid is True
    assert quality.is_visual_hook is True
    assert quality.reject_reasons == ()


@pytest.mark.unit
def test_validate_rejects_low_information_dialogue():
    segs = [
        {"start_time": 30.0, "end_time": 32.0, "visual": "男人点头", "copy": "嗯。"},
        {"start_time": 32.0, "end_time": 35.0, "visual": "女人看向门口", "copy": "走吧。"},
    ]

    validation = validate_start_candidate(segs[0], segs, content_start=0.0)
    quality = score_start_candidate(segs[0], segs, content_start=0.0)

    assert validation.is_valid is False
    assert "weak_dialogue" in quality.reject_reasons


@pytest.mark.unit
def test_fallback_skips_empty_and_plain_segments():
    from Flowcut.runtime.highlight_start_select import _find_fallback_pick

    segs = [
        {"start_time": 0.0, "end_time": 3.0, "visual": "街道空镜，环境过渡", "copy": ""},
        {"start_time": 3.0, "end_time": 5.0, "visual": "男人点头", "copy": "好。"},
        {"start_time": 5.0, "end_time": 9.0, "visual": "女人当众揭穿真相，双方激烈争执", "copy": "你早就知道这一切，对不对？"},
    ]

    pick = _find_fallback_pick(segs, content_start=0.0)

    assert pick is not None
    assert pick["idx"] == 2
    assert pick["candidate_quality"]["reject_reasons"] == []


@pytest.mark.unit
def test_select_picks_primary_hook_and_maps_to_episode():
    segs = [
        _seg(2.0, 5.0, hook=3.0, use="secondary_hook"),
        _seg(45.0, 48.0, hook=9.0, use="primary_hook"),  # 全局45s → 第3集第4秒
    ]
    out = select_start_candidates(segs, OFFSETS, DURATIONS, top_n=3)
    assert out[0].episode_no == 3
    assert out[0].local_start == pytest.approx(4.0)
    assert out[0].global_start == pytest.approx(45.0)


@pytest.mark.unit
def test_select_dedups_near_starts():
    segs = [
        _seg(45.0, 48.0, hook=9.0, use="primary_hook"),
        _seg(46.0, 49.0, hook=8.0, use="primary_hook"),  # 距上一个 <2s，丢弃
        _seg(10.0, 13.0, hook=7.0, use="primary_hook"),
    ]
    out = select_start_candidates(segs, OFFSETS, DURATIONS, top_n=3)
    starts = sorted(c.global_start for c in out)
    assert starts == pytest.approx([10.0, 45.0])


@pytest.mark.unit
def test_select_respects_top_n():
    segs = [_seg(float(i * 5), float(i * 5 + 3), hook=float(10 - i),
                 use="primary_hook") for i in range(6)]
    out = select_start_candidates(segs, OFFSETS, DURATIONS, top_n=2)
    assert len(out) == 2


@pytest.mark.unit
def test_build_timeline_clips_start_episode_and_accumulates():
    ep3 = EpisodeRef(asset_id=30, episode_no=3, oss_key="k3", duration=19.0)
    ep4 = EpisodeRef(asset_id=40, episode_no=4, oss_key="k4", duration=20.0)
    ep3_segs = [_seg(0.0, 4.0), _seg(4.0, 10.0, copy="甲：你来了。"),
                _seg(10.0, 19.0, copy="乙：是的。")]
    ep4_segs = [_seg(0.0, 8.0, copy="丙：走吧。"), _seg(8.0, 20.0)]
    tl = build_timeline(4.0, [(ep3, ep3_segs), (ep4, ep4_segs)])
    # 起点集只保留 seg_end > 4.0 的段；首段被裁到 4.0 开始
    assert tl[0].episode_no == 3
    assert tl[0].seg_start == pytest.approx(4.0)
    assert tl[0].cum_start == pytest.approx(0.0)
    assert tl[0].cum_end == pytest.approx(6.0)   # (10-4)
    # 跨集后 cum 连续累加
    assert tl[-1].episode_no == 4
    assert tl[-1].cum_end == pytest.approx(6.0 + 9.0 + 8.0 + 12.0)


@pytest.mark.unit
def test_build_timeline_uses_real_time_not_segment_sum():
    # 长集(77s) + 稀疏分镜(段间有大空隙)：cum 必须按真实时间轴，不能按段长累加
    ep3 = EpisodeRef(asset_id=30, episode_no=3, oss_key="k3", duration=77.0)
    ep4 = EpisodeRef(asset_id=40, episode_no=4, oss_key="k4", duration=20.0)
    ep3_segs = [_seg(2.0, 5.0, copy="开头"), _seg(60.0, 63.0, copy="后面。")]  # 5..60 是空隙
    ep4_segs = [_seg(0.0, 4.0, copy="下一集")]
    tl = build_timeline(2.0, [(ep3, ep3_segs), (ep4, ep4_segs)])
    # 第二段真实结束在 ep3 第63秒，相对起点(2s) cum=61s —— 不是段长累加(3+3=6)
    assert tl[1].episode_no == 3
    assert tl[1].cum_end == pytest.approx(61.0)
    # ep4 段：real_offset = 77-2 = 75；其 cum=75+4=79（不是 6+4=10）
    assert tl[2].episode_no == 4
    assert tl[2].cum_end == pytest.approx(79.0)


@pytest.mark.unit
def test_resolve_real_end_within_first_episode():
    # 长集(77s)起点@2s，target 60s → 落在同一集第62s，单集收尾
    eps = [EpisodeRef(asset_id=30, episode_no=3, oss_key="k3", duration=77.0)]
    b = resolve_real_end(2.0, eps, 60.0)
    assert b.episode_no == 3
    assert b.local_end == pytest.approx(62.0)
    assert b.cum_time == pytest.approx(60.0)
    assert b.boundary_type == "hard_cut"


@pytest.mark.unit
def test_resolve_real_end_crosses_episodes():
    # 起点@7s，第一集只剩3s(dur10)，target 15s → 跨到 ep4 第12s
    eps = [
        EpisodeRef(asset_id=30, episode_no=3, oss_key="k3", duration=10.0),
        EpisodeRef(asset_id=40, episode_no=4, oss_key="k4", duration=20.0),
    ]
    b = resolve_real_end(7.0, eps, 15.0)
    assert b.episode_no == 4
    assert b.local_end == pytest.approx(12.0)


@pytest.mark.unit
def test_resolve_real_end_overflow_lands_last_episode_end():
    eps = [EpisodeRef(asset_id=30, episode_no=3, oss_key="k3", duration=10.0)]
    b = resolve_real_end(2.0, eps, 100.0)  # 远超可用(8s)
    assert b.episode_no == 3
    assert b.local_end == pytest.approx(10.0)


def _tl(*triples):
    """triples: (cum_start, cum_end, copy) → TimedSegment(全归到第9集，local=cum)。"""
    return [TimedSegment(episode_no=9, seg_start=cs, seg_end=ce,
                         cum_start=cs, cum_end=ce, copy=cp)
            for cs, ce, cp in triples]


@pytest.mark.unit
def test_pick_prefers_sentence_end_nearest_ideal():
    tl = _tl((48, 52, "甲：还没说完"), (52, 58, "乙：这句说完了。"),
             (58, 63, "丙：另一句也完了！"), (63, 69, "丁：太晚了。"))
    b = pick_end_boundary(tl)
    assert b.boundary_type == "sentence"
    assert b.cum_time == pytest.approx(58.0)  # 句末安全边界 58/63/69 里，58 距 60 最近


@pytest.mark.unit
def test_pick_falls_back_to_shot_when_no_sentence():
    tl = _tl((40, 55, "话说到一半"), (55, 62, "还没完整"), (62, 80, "更长"))
    b = pick_end_boundary(tl)
    assert b.boundary_type == "shot"
    assert b.cum_time == pytest.approx(62.0)  # 窗口内最接近60的分镜切点


@pytest.mark.unit
def test_pick_hard_cut_when_no_boundary_in_window():
    tl = _tl((10, 90, "一个超长镜头横跨整个窗口"))
    b = pick_end_boundary(tl)
    assert b.boundary_type == "hard_cut"
    assert b.cum_time == pytest.approx(60.0)
    assert b.local_end == pytest.approx(60.0)  # local = seg_start + (60-cum_start)


@pytest.mark.unit
def test_pick_snaps_to_boundary_at_47s_not_hard_cut():
    """放宽到 ±15s 窗口后：60s 附近唯一的逻辑切点在 47s（旧 [50,70] 窗外）
    时应吸附过去，而不是在 60s 硬切。"""
    tl = _tl((0, 47, "这句话讲完了。"), (47, 90, "之后是一个超长镜头"))
    b = pick_end_boundary(tl)
    assert b.boundary_type == "sentence"
    assert b.cum_time == pytest.approx(47.0)


@pytest.mark.unit
def test_build_clip_plan_spans_three_episodes():
    eps = [
        EpisodeRef(asset_id=30, episode_no=3, oss_key="k3", duration=19.0),
        EpisodeRef(asset_id=40, episode_no=4, oss_key="k4", duration=20.0),
        EpisodeRef(asset_id=50, episode_no=5, oss_key="k5", duration=20.0),
    ]
    start = StartCandidate(episode_no=3, local_start=4.0, global_start=45.0,
                           hook_strength=9.0, reason="冲突爆发")
    end = EndBoundary(cum_time=58.0, episode_no=5, local_end=23.0,
                      boundary_type="sentence")
    plan = build_clip_plan(start, end, eps)
    assert [(e.episode_no, e.cut_start, e.cut_end) for e in plan.entries] == [
        (3, 4.0, 19.0),   # 起点集：start → 集末
        (4, 0.0, 20.0),   # 中间集：整集
        (5, 0.0, 23.0),   # 收尾集：0 → local_end
    ]
    assert plan.boundary_type == "sentence"
    assert plan.start_episode_no == 3


@pytest.mark.unit
def test_build_clip_plan_single_episode():
    eps = [EpisodeRef(asset_id=30, episode_no=3, oss_key="k3", duration=19.0)]
    start = StartCandidate(3, 4.0, 45.0, 9.0, "r")
    end = EndBoundary(12.0, 3, 16.0, "shot")
    plan = build_clip_plan(start, end, eps)
    assert [(e.episode_no, e.cut_start, e.cut_end) for e in plan.entries] == [
        (3, 4.0, 16.0),
    ]


@pytest.mark.unit
def test_select_all_context_only_returns_empty():
    segs = [
        _seg(2.0, 5.0, hook=9.0, use="context_only"),
        _seg(10.0, 15.0, hook=7.0, use="context_only"),
        _seg(20.0, 25.0, hook=5.0, use="context_only"),
    ]
    out = select_start_candidates(segs, OFFSETS, DURATIONS, top_n=3)
    assert out == []


@pytest.mark.unit
def test_pick_end_boundary_ideal_beyond_timeline():
    # 最后一段 cum_end=30，ideal=60 超出时间线末尾 → hard_cut，取最后一段末尾
    tl = _tl((0, 10, "第一段"), (10, 20, "第二段。"), (20, 30, "第三段。"))
    b = pick_end_boundary(tl)
    assert b.boundary_type == "hard_cut"
    assert b.cum_time == pytest.approx(30.0)
    assert b.local_end == pytest.approx(30.0)  # seg_end 与 cum_end 重合（_tl helper）


@pytest.mark.unit
def test_build_clip_plan_filters_out_of_range_episodes():
    eps = [
        EpisodeRef(asset_id=10, episode_no=1, oss_key="k1", duration=20.0),  # 早于起点集，应过滤
        EpisodeRef(asset_id=30, episode_no=3, oss_key="k3", duration=19.0),  # 起点集
        EpisodeRef(asset_id=40, episode_no=4, oss_key="k4", duration=20.0),  # 收尾集
        EpisodeRef(asset_id=60, episode_no=6, oss_key="k6", duration=18.0),  # 晚于收尾集，应过滤
    ]
    start = StartCandidate(episode_no=3, local_start=4.0, global_start=45.0,
                           hook_strength=9.0, reason="冲突爆发")
    end = EndBoundary(cum_time=55.0, episode_no=4, local_end=16.0,
                      boundary_type="sentence")
    plan = build_clip_plan(start, end, eps)
    episode_nos = [e.episode_no for e in plan.entries]
    assert episode_nos == [3, 4]
