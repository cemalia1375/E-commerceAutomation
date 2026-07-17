"""逆向对齐的纯算法层（无 IO，可完整单测）。

输入是「跑量切片」与「原片各集」各自的拆镜 segment 列表
（来自 gemini_video.analyze_video 的 {start_time,end_time,visual,copy,category}），
输出是：每个跑量段落定位回原片的 (集, 集内时间)，以及重排/钩子/删减等指标。

所有相似度只用标准库 difflib，对 AI 短剧逐字台词足够；不依赖外部向量服务。
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

# 台词去噪：标点 / 空白都不参与匹配
_PUNCT = set("，。！？、；：…—·.,!?;:\"'“”‘’()（）【】[]{}<>《》~`@#$%^&*-_=+|\\/\n\t 　")
COPY_MIN_LEN = 4          # 台词短于此长度时不足以作为锚点，转用画面
COPY_MATCH_THRESHOLD = 0.6
VISUAL_MATCH_THRESHOLD = 0.5
_EP_STRIDE = 1_000_000.0  # 线性化全局位置：episode_no * stride + start_time


def normalize(text: str) -> str:
    """去掉标点和空白，保留实义字符，用于台词逐字比对。"""
    return "".join(ch for ch in (text or "") if ch not in _PUNCT)


def similarity(a: str, b: str) -> float:
    """归一化后的相似度：取「整体 ratio」与「最长公共子串占比」的较大值。

    取 max 是为了让「短台词是长台词的逐字子串」也能拿高分（切片常只截原句一部分）。
    """
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    sm = SequenceMatcher(None, na, nb, autojunk=False)
    full = sm.ratio()
    block = sm.find_longest_match(0, len(na), 0, len(nb)).size
    partial = block / min(len(na), len(nb))
    return max(full, partial)


@dataclass(frozen=True)
class OriginSegment:
    episode_no: int
    start_time: float
    end_time: float
    visual: str
    copy: str

    @property
    def global_pos(self) -> float:
        return self.episode_no * _EP_STRIDE + self.start_time


def to_origin_segments(per_episode: dict[int, list[dict]]) -> list[OriginSegment]:
    """把 {episode_no: [seg,...]} 展平成 OriginSegment 列表。"""
    out: list[OriginSegment] = []
    for ep_no, segs in sorted(per_episode.items()):
        for s in segs:
            out.append(OriginSegment(
                episode_no=ep_no,
                start_time=float(s.get("start_time") or 0.0),
                end_time=float(s.get("end_time") or 0.0),
                visual=str(s.get("visual") or ""),
                copy=str(s.get("copy") or ""),
            ))
    return out


@dataclass(frozen=True)
class Match:
    ad_idx: int
    ad_start: float
    ad_end: float
    ad_copy: str
    signal: str          # 'copy' | 'visual' | 'none'
    score: float
    episode_no: int | None
    origin_start: float | None
    origin_end: float | None
    origin_copy: str

    @property
    def matched(self) -> bool:
        return self.signal != "none"

    @property
    def global_pos(self) -> float | None:
        if self.episode_no is None or self.origin_start is None:
            return None
        return self.episode_no * _EP_STRIDE + self.origin_start


def _best(ad_text: str, origins: list[OriginSegment], field: str) -> tuple[float, OriginSegment | None]:
    best_score, best_seg = 0.0, None
    for o in origins:
        s = similarity(ad_text, getattr(o, field))
        if s > best_score:
            best_score, best_seg = s, o
    return best_score, best_seg


def match_ad_segment(ad_idx: int, ad_seg: dict, origins: list[OriginSegment]) -> Match:
    """单个跑量段落定位回原片：台词够长优先按台词配，否则按画面配。"""
    ad_start = float(ad_seg.get("start_time") or 0.0)
    ad_end = float(ad_seg.get("end_time") or 0.0)
    ad_copy = str(ad_seg.get("copy") or "")
    ad_visual = str(ad_seg.get("visual") or "")

    use_copy = len(normalize(ad_copy)) >= COPY_MIN_LEN
    if use_copy:
        score, seg = _best(ad_copy, origins, "copy")
        if seg is not None and score >= COPY_MATCH_THRESHOLD:
            return Match(ad_idx, ad_start, ad_end, ad_copy, "copy", score,
                         seg.episode_no, seg.start_time, seg.end_time, seg.copy)

    # 台词为空 / 太短 / 没配上 → 退到画面描述
    vscore, vseg = _best(ad_visual, origins, "visual")
    if vseg is not None and vscore >= VISUAL_MATCH_THRESHOLD:
        return Match(ad_idx, ad_start, ad_end, ad_copy, "visual", vscore,
                     vseg.episode_no, vseg.start_time, vseg.end_time, vseg.copy)

    return Match(ad_idx, ad_start, ad_end, ad_copy, "none", 0.0, None, None, None, "")


def build_alignment(ad_segs: list[dict], origins: list[OriginSegment]) -> list[Match]:
    return [match_ad_segment(i, seg, origins) for i, seg in enumerate(ad_segs)]


@dataclass(frozen=True)
class ReorderMetrics:
    total_segments: int
    matched_segments: int
    coverage_ratio: float          # 已匹配段时长 / 跑量切片总时长
    episodes_used: tuple[int, ...]
    hook_global_pos: float | None  # 切片首段在原片的全局位置
    hook_is_front_loaded: bool     # 首段是否取自比最早匹配点更靠后的位置
    hook_episode: int | None
    earliest_episode: int | None
    order_fidelity: float          # 1 - 逆序对占比；1=完全顺序，0=完全打乱
    origin_drop_ratio: float       # 原片(前N集)台词段中未被任何切片段命中的比例


def _order_fidelity(positions: list[float]) -> float:
    n = len(positions)
    if n < 2:
        return 1.0
    pairs = n * (n - 1) / 2
    inv = sum(
        1
        for i in range(n)
        for j in range(i + 1, n)
        if positions[i] > positions[j]
    )
    return 1.0 - inv / pairs


def reorder_metrics(
    alignment: list[Match],
    origins: list[OriginSegment],
    ad_total_duration: float,
) -> ReorderMetrics:
    matched = [m for m in alignment if m.matched]
    matched_dur = sum(m.ad_end - m.ad_start for m in matched)
    eps = sorted({m.episode_no for m in matched if m.episode_no is not None})

    positions = [m.global_pos for m in matched if m.global_pos is not None]
    earliest_ep = min(eps) if eps else None

    hook = next((m for m in alignment if m.matched), None)
    hook_pos = hook.global_pos if hook else None
    hook_ep = hook.episode_no if hook else None
    front_loaded = bool(hook_pos is not None and positions and hook_pos > min(positions))

    # 原片台词段被切片覆盖的比例：以原片有台词的段为分母
    origin_copy_segs = [o for o in origins if len(normalize(o.copy)) >= COPY_MIN_LEN]
    hit_keys = {
        (m.episode_no, round(m.origin_start, 2))
        for m in matched
        if m.signal == "copy" and m.episode_no is not None and m.origin_start is not None
    }
    if origin_copy_segs:
        hit = sum(1 for o in origin_copy_segs if (o.episode_no, round(o.start_time, 2)) in hit_keys)
        drop_ratio = 1.0 - hit / len(origin_copy_segs)
    else:
        drop_ratio = 0.0

    return ReorderMetrics(
        total_segments=len(alignment),
        matched_segments=len(matched),
        coverage_ratio=(matched_dur / ad_total_duration) if ad_total_duration > 0 else 0.0,
        episodes_used=tuple(eps),
        hook_global_pos=hook_pos,
        hook_is_front_loaded=front_loaded,
        hook_episode=hook_ep,
        earliest_episode=earliest_ep,
        order_fidelity=_order_fidelity(positions),
        origin_drop_ratio=drop_ratio,
    )
