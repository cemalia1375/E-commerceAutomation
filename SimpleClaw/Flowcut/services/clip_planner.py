"""跨集高光切片的纯算法层（无 IO，可完整单测）。

输入是已解好的 segment 字典（来自 gemini_video.analyze_* 输出），
输出是选好的起点、跨集时间线、收尾边界、各集裁切计划。
所有 IO（下载/Gemini/ffmpeg/落库/入队）由 executor 负责，本模块不碰。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

START_SEARCH_EPISODES = 3
WINDOW = (45.0, 75.0)
IDEAL = 60.0
DEFAULT_CANDIDATES = 3
MAX_FORWARD_EPISODES = 6
DEDUP_GAP_S = 2.0
SENTENCE_PUNCT = "。！？…!?."
_PRIMARY = {"primary_hook", "secondary_hook"}

# ── 片头过滤 & 上下文扩展常量 ──
PRE_ROLL_S = 3.0        # 前扩展秒数（避免对白开头被截断）
POST_ROLL_S = 2.0       # 后扩展秒数（保证对白完整收尾）
MIN_DIALOGUE_CHARS = 3   # 最低对白字符数（低于此值视为无对白/片头）
MIN_DIALOGUE_SEGMENTS = 2  # 高光起点附近至少需要的对白段数
DIALOGUE_LOOKAHEAD_S = 15.0  # 前瞻窗口：检查起点后多少秒内的对白密度
OP_ESTIMATE_MAX_S = 35.0  # OP 长度估算上限（秒）

logger = logging.getLogger(__name__)


def match_drama_episodes(rows: list[dict], drama_name: str) -> list[dict]:
    """精确匹配落空时的回退：按剧名子串匹配 episode_source 行，按 episode_no 排序。

    LLM 传入的剧名常与库里存的不完全一致（如缺少「被」前缀），子串匹配可命中
    （子串同时匹配 drama_name 或 name 字段）。
    """
    q = (drama_name or "").strip().lower()
    if not q:
        return []
    matched = [
        r for r in rows
        if q in str(r.get("drama_name") or "").lower()
        or q in str(r.get("name") or "").lower()
    ]
    return sorted(matched, key=lambda r: int(r.get("episode_no") or 0))


def detect_content_start(
    segments: list[dict],
    *,
    min_dialogue_chars: int = MIN_DIALOGUE_CHARS,
) -> float:
    """检测实际剧情内容的起始时间（跳过片头 Logo/标题/OP）。

    策略：
    1. 找到第一条包含有意义对白（>= min_dialogue_chars 个字符）的 segment。
    2. 如果前面有无对白段（visual 可能是片头），从第一个有对白段开始。
    3. 同时检测字幕密度：若前 N 秒字幕密度显著低于后面，则前 N 秒很可能是 OP。
    4. 返回 content_start_time（秒），所有高光不得早于此时间。

    若全片无对白，返回 0.0（不回退任何内容）。
    """
    if not segments:
        return 0.0

    sorted_segs = sorted(segments, key=lambda s: float(s.get("start_time") or 0.0))

    # 策略 1 & 2：找到第一条有对白的段
    first_dialogue_time: float | None = None
    for seg in sorted_segs:
        copy_text = str(seg.get("copy", "")).strip()
        if len(copy_text) >= min_dialogue_chars:
            first_dialogue_time = float(seg.get("start_time", 0.0))
            break

    if first_dialogue_time is None:
        return 0.0  # 全片无对白，不限制

    # 策略 3：字幕密度检测。如果某段之前的所有段都没有对白，
    # 则 content_start 取第一个有对白段的 start_time。
    # 同时检查 visual 是否包含片头关键词（辅助判断）。
    opening_keywords = ["标题", "出品", "制作", "logo", "片头", "演职员", "原著"]
    last_opening_time = 0.0
    for seg in sorted_segs:
        seg_start = float(seg.get("start_time", 0.0))
        if seg_start >= first_dialogue_time:
            break
        visual = str(seg.get("visual", "")).lower()
        copy_text = str(seg.get("copy", "")).strip()
        # 如果 visual 包含片头关键词且无对白，标记为 OP 候选
        if any(kw in visual for kw in opening_keywords) and len(copy_text) < min_dialogue_chars:
            last_opening_time = max(last_opening_time, float(seg.get("end_time", seg_start)))

    # 取较保守的 content_start：第一个有对白段，或最后一个片头标记段的结束
    content_start = max(first_dialogue_time, last_opening_time)

    # 策略 4：上限保护。content_start 不超过 OP_ESTIMATE_MAX_S
    if content_start > OP_ESTIMATE_MAX_S:
        # 如果超过上限，可能是有长静默开头但不是 OP；放宽到第一个对白时间
        content_start = first_dialogue_time

    return round(content_start, 2)


@dataclass(frozen=True)
class ContentValidation:
    """对高光起点的有效性校验结果。"""
    is_valid: bool
    reason: str = ""
    suggested_start: float | None = None  # 若无效，建议的替代起点（集内秒）


@dataclass(frozen=True)
class CandidateQuality:
    """Code-side quality signals for a highlight opening candidate."""
    hook_score: float
    reject_reasons: tuple[str, ...] = field(default_factory=tuple)
    downrank_reasons: tuple[str, ...] = field(default_factory=tuple)
    is_empty_opening: bool = False
    is_transition: bool = False
    is_visual_hook: bool = False
    weak_dialogue: bool = False
    context_dependency: float = 0.0

    @property
    def is_rejected(self) -> bool:
        return bool(self.reject_reasons)

    def to_dict(self) -> dict:
        return {
            "hook_score": round(self.hook_score, 3),
            "reject_reasons": list(self.reject_reasons),
            "downrank_reasons": list(self.downrank_reasons),
            "is_empty_opening": self.is_empty_opening,
            "is_transition": self.is_transition,
            "is_visual_hook": self.is_visual_hook,
            "weak_dialogue": self.weak_dialogue,
            "context_dependency": round(self.context_dependency, 3),
        }


_TRANSITION_VISUAL_KEYWORDS = (
    "空镜", "环境", "远景", "街景", "夜景", "天空", "建筑", "走廊", "门口",
    "外景", "风景", "转场", "过渡", "黑场", "淡入", "淡出", "logo",
    "标题", "片头", "出品", "制作", "演职员", "字幕介绍",
)
_STRONG_VISUAL_HOOK_KEYWORDS = (
    "掌掴", "扇耳光", "下跪", "跪下", "打斗", "扭打", "推倒", "摔倒",
    "砸", "砸碎", "撞", "车祸", "爆炸", "流血", "拿刀", "持刀", "追赶",
    "抓住", "掐住", "拽住", "撕扯", "晕倒", "昏倒", "崩溃", "痛哭",
    "怒吼", "对峙", "冲突", "争执", "爆发",
)
_CONFLICT_KEYWORDS = (
    "背叛", "复仇", "离婚", "威胁", "报复", "真相", "秘密", "陷害",
    "揭穿", "争吵", "质问", "崩溃", "绝望", "不配", "滚", "跪",
)
_WEAK_DIALOGUE = {
    "嗯", "啊", "哦", "好", "行", "对", "是", "可以", "知道了", "没事",
    "喂", "来了", "走吧", "谢谢", "不用", "没关系",
}
_CONTEXT_DEPENDENT_PREFIXES = (
    "所以", "原来", "为什么", "你为什么", "既然", "难道", "那你",
    "这么说", "也就是说", "怪不得", "你早就", "这一切", "你刚才",
)


def _compact_dialogue(text: str) -> str:
    return "".join(ch for ch in text.strip() if ch not in " ，。！？!?…,.、：:“”\"' ")


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def score_start_candidate(
    shot: dict,
    all_segments: list[dict],
    content_start: float,
    *,
    llm_hook_strength: float = 0.0,
    min_dialogue_chars: int = MIN_DIALOGUE_CHARS,
    dialogue_lookahead_s: float = DIALOGUE_LOOKAHEAD_S,
) -> CandidateQuality:
    """Score and classify one opening candidate without calling external services."""
    start = float(shot.get("start_time", 0.0))
    visual = str(shot.get("visual") or "")
    copy_text = str(shot.get("copy") or "").strip()
    compact_copy = _compact_dialogue(copy_text)
    combined = f"{visual} {copy_text}"

    is_transition = _contains_any(visual, _TRANSITION_VISUAL_KEYWORDS)
    is_visual_hook = _contains_any(combined, _STRONG_VISUAL_HOOK_KEYWORDS)
    weak_dialogue = bool(compact_copy) and (
        compact_copy in _WEAK_DIALOGUE or len(compact_copy) < min_dialogue_chars
    )
    no_meaningful_dialogue = len(compact_copy) < min_dialogue_chars
    is_empty_opening = no_meaningful_dialogue and is_transition and not is_visual_hook

    context_dependency = float(shot.get("context_dependency") or 0.0)
    if any(compact_copy.startswith(prefix) for prefix in _CONTEXT_DEPENDENT_PREFIXES):
        context_dependency = max(context_dependency, 0.8)

    reject: list[str] = []
    downrank: list[str] = []
    if content_start > 0 and start < content_start:
        reject.append("opening_before_content_start")
    if is_empty_opening:
        reject.append("empty_opening")
    elif is_transition and no_meaningful_dialogue and not is_visual_hook:
        reject.append("transition_shot")
    if weak_dialogue and not is_visual_hook:
        reject.append("weak_dialogue")

    nearby_dialogue = _count_nearby_dialogue_segments(
        all_segments, start, dialogue_lookahead_s, min_dialogue_chars
    )
    if no_meaningful_dialogue and not is_visual_hook and nearby_dialogue < MIN_DIALOGUE_SEGMENTS:
        reject.append("no_meaningful_dialogue")
    if context_dependency >= 0.75:
        downrank.append("high_context_dependency")

    score = float(llm_hook_strength or shot.get("hook_strength") or 0.0)
    if is_visual_hook:
        score += 3.0
    if len(compact_copy) >= min_dialogue_chars:
        score += min(2.0, len(compact_copy) / 20.0)
    if _contains_any(combined, _CONFLICT_KEYWORDS):
        score += 1.5
    if is_transition:
        score -= 2.5
    if weak_dialogue:
        score -= 2.0
    if context_dependency >= 0.75:
        score -= 1.0

    return CandidateQuality(
        hook_score=max(0.0, score),
        reject_reasons=tuple(dict.fromkeys(reject)),
        downrank_reasons=tuple(dict.fromkeys(downrank)),
        is_empty_opening=is_empty_opening,
        is_transition=is_transition,
        is_visual_hook=is_visual_hook,
        weak_dialogue=weak_dialogue,
        context_dependency=context_dependency,
    )


def validate_start_candidate(
    shot: dict,
    all_segments: list[dict],
    content_start: float,
    *,
    min_dialogue_chars: int = MIN_DIALOGUE_CHARS,
    dialogue_lookahead_s: float = DIALOGUE_LOOKAHEAD_S,
) -> ContentValidation:
    """校验一个高光起点候选是否落在有效剧情区域。

    检查项：
    1. 是否落在片头区域（start_time < content_start）。
    2. 当前段及后续段的对话是否充足。
    3. visual 是否描述了片头/标题元素。

    Returns:
        ContentValidation: 包含有效性、原因和可选替代起点。
    """
    start = float(shot.get("start_time", 0.0))
    copy_text = str(shot.get("copy", "")).strip()
    visual = str(shot.get("visual", "")).lower()
    end = float(shot.get("end_time", start + 1.0))
    quality = score_start_candidate(
        shot,
        all_segments,
        content_start,
        llm_hook_strength=float(shot.get("hook_strength") or 0.0),
        min_dialogue_chars=min_dialogue_chars,
        dialogue_lookahead_s=dialogue_lookahead_s,
    )

    # ── 检查 1：片头区域 ──
    if content_start > 0 and start < content_start:
        return ContentValidation(
            is_valid=False,
            reason=(
                f"高光起点 {start:.1f}s 落在片头区域 "
                f"(content_start={content_start:.1f}s)"
            ),
            suggested_start=content_start,
        )

    if quality.is_empty_opening or "transition_shot" in quality.reject_reasons:
        alt = _find_nearest_dialogue_segment(
            all_segments, end, content_start, min_dialogue_chars
        )
        return ContentValidation(
            is_valid=False,
            reason=(
                f"高光起点 {start:.1f}s 是空镜/过渡镜头 "
                f"({','.join(quality.reject_reasons)})"
            ),
            suggested_start=alt,
        )

    if "weak_dialogue" in quality.reject_reasons:
        alt = _find_nearest_dialogue_segment(
            all_segments, end, content_start, min_dialogue_chars
        )
        return ContentValidation(
            is_valid=False,
            reason=f"高光起点 {start:.1f}s 是低信息对白",
            suggested_start=alt,
        )

    # ── 检查 2：对白数量 ──
    if len(copy_text) < min_dialogue_chars:
        if quality.is_visual_hook:
            return ContentValidation(is_valid=True, reason="强视觉 hook 校验通过")
        # 检查附近段是否有对白
        nearby_dialogue = _count_nearby_dialogue_segments(
            all_segments, start, dialogue_lookahead_s, min_dialogue_chars
        )
        if nearby_dialogue < MIN_DIALOGUE_SEGMENTS:
            # 尝试找一个有对白的附近段作为替代起点
            alt = _find_nearest_dialogue_segment(
                all_segments, start, content_start, min_dialogue_chars
            )
            return ContentValidation(
                is_valid=False,
                reason=(
                    f"高光起点 {start:.1f}s 对白为空且附近 {dialogue_lookahead_s:.0f}s 内"
                    f"仅有 {nearby_dialogue} 段对白（需 >= {MIN_DIALOGUE_SEGMENTS}）"
                ),
                suggested_start=alt,
            )

    # ── 检查 3：片头 visual 关键词 ──
    opening_keywords = ["标题", "出品", "制作", "logo", "片头", "演职员", "原著"]
    hit_keywords = [kw for kw in opening_keywords if kw in visual]
    if hit_keywords and len(copy_text) < min_dialogue_chars:
        alt = _find_nearest_dialogue_segment(
            all_segments, end, content_start, min_dialogue_chars
        )
        return ContentValidation(
            is_valid=False,
            reason=(
                f"高光起点 visual 包含片头关键词 {hit_keywords}，"
                f"且无对白"
            ),
            suggested_start=alt,
        )

    return ContentValidation(is_valid=True, reason="校验通过")


def _count_nearby_dialogue_segments(
    segments: list[dict],
    from_time: float,
    window_s: float,
    min_chars: int,
) -> int:
    """统计 from_time 起 window_s 秒内有多少段有意义的对白。"""
    count = 0
    for seg in segments:
        seg_start = float(seg.get("start_time", 0.0))
        if seg_start < from_time:
            continue
        if seg_start > from_time + window_s:
            break
        copy_text = str(seg.get("copy", "")).strip()
        if len(copy_text) >= min_chars:
            count += 1
    return count


def _find_nearest_dialogue_segment(
    segments: list[dict],
    from_time: float,
    content_start: float,
    min_chars: int,
) -> float | None:
    """在 from_time 之后（但不早于 content_start）找最近的有对白段的 start_time。

    优先 from_time 之后的段，若没有则返回 content_start。
    """
    best: float | None = None
    for seg in sorted(segments, key=lambda s: float(s.get("start_time") or 0.0)):
        seg_start = float(seg.get("start_time", 0.0))
        if seg_start < max(from_time, content_start):
            continue
        copy_text = str(seg.get("copy", "")).strip()
        if len(copy_text) >= min_chars:
            best = seg_start
            break
    if best is None:
        best = max(from_time, content_start)
    return best


def expand_start_with_context(
    local_start: float,
    content_start: float,
    *,
    pre_roll_s: float = PRE_ROLL_S,
) -> tuple[float, str]:
    """对高光起点做上下文前扩展。

    在 Gemini 选中的 local_start 基础上向前扩展 pre_roll_s 秒，
    但绝不跨越 content_start（防止进入片头）。

    Returns:
        (expanded_start, log_message)
    """
    if pre_roll_s <= 0:
        return local_start, ""

    lower_bound = max(0.0, content_start)
    ideal = local_start - pre_roll_s
    if ideal < lower_bound:
        # 扩展会进入片头区域 → 仅扩展到 content_start
        clamped = lower_bound
        return clamped, (
            f"前扩展受限：ideal={ideal:.1f}s 会进入片头 "
            f"(content_start={content_start:.1f}s)，clamp 到 {clamped:.1f}s"
        )
    else:
        return ideal, (
            f"前扩展：{local_start:.1f}s → {ideal:.1f}s "
            f"(-{pre_roll_s:.1f}s，content_start={content_start:.1f}s)"
        )


@dataclass(frozen=True)
class EpisodeRef:
    asset_id: int
    episode_no: int
    oss_key: str
    duration: float  # 秒


@dataclass(frozen=True)
class StartCandidate:
    episode_no: int
    local_start: float
    global_start: float
    hook_strength: float
    reason: str


def _locate(global_t: float, offsets: list[tuple[int, float]],
            durations: dict[int, float]) -> tuple[int, float] | None:
    """把前3集拼接视频里的全局秒数映射回 (episode_no, 集内秒)。"""
    for ep_no, off in offsets:
        dur = durations.get(ep_no, 0.0)
        if off <= global_t < off + dur:
            return ep_no, global_t - off
    # 落在最后一集末尾的边界容差
    if offsets:
        ep_no, off = offsets[-1]
        return ep_no, max(0.0, global_t - off)
    return None


def locate(global_t: float, offsets: list[tuple[int, float]],
           durations: dict[int, float]) -> tuple[int, float] | None:
    """公开包装 _locate：把合并视频里的全局秒映射回 (episode_no, 集内秒)。"""
    return _locate(global_t, offsets, durations)


def select_start_candidates(
    segments: list[dict],
    offsets: list[tuple[int, float]],
    durations: dict[int, float],
    top_n: int,
) -> list[StartCandidate]:
    """从前3集合并解出的 segments 里选 top-N 高光起点。

    排序：primary/secondary_hook 优先 → hook_strength 高 → context_dependency 低
    → continuity_risk 低。去重：全局起点相距 < DEDUP_GAP_S 的只留排名更高者。
    """
    ranked = sorted(
        (s for s in segments if str(s.get("candidate_use")) in _PRIMARY),
        key=lambda s: (
            0 if str(s.get("candidate_use")) == "primary_hook" else 1,
            -float(s.get("hook_strength") or 0.0),
            float(s.get("context_dependency") or 0.0),
            float(s.get("continuity_risk") or 0.0),
        ),
    )
    out: list[StartCandidate] = []
    for s in ranked:
        g = float(s.get("start_time") or 0.0)
        if any(abs(g - c.global_start) < DEDUP_GAP_S for c in out):
            continue
        loc = _locate(g, offsets, durations)
        if loc is None:
            continue
        ep_no, local = loc
        out.append(StartCandidate(
            episode_no=ep_no,
            local_start=local,
            global_start=g,
            hook_strength=float(s.get("hook_strength") or 0.0),
            reason=str(s.get("reason") or ""),
        ))
        if len(out) >= top_n:
            break
    return out


@dataclass(frozen=True)
class TimedSegment:
    episode_no: int
    seg_start: float  # 集内秒
    seg_end: float    # 集内秒
    cum_start: float  # 从切片起点(=0)起的全局累计
    cum_end: float
    copy: str


def build_timeline(
    start_local: float,
    ordered_eps: list[tuple["EpisodeRef", list[dict]]],
) -> list[TimedSegment]:
    """从起点集的 start_local 起，按集顺序拼成带累计时间的 segment 列表。

    第一个元素必须是起点集：只保留 seg_end > start_local 的段，首段裁到 start_local。
    后续集全部纳入。

    cum 按**真实时间轴**累计（不是分镜段长累加）：每个 segment 的 cum 由「该集相对
    切片起点的真实偏移 + segment 在集内的真实位置」决定；跨集时整段真实集时长推进
    real_offset。这样 Gemini 分镜稀疏/有空隙时不会低估真实时长、误判跨集（长集应落
    单集，短集才会真正跨集）。
    """
    out: list[TimedSegment] = []
    real_offset = 0.0  # 当前集起点相对切片起点的真实累计时间
    for i, (ep, segs) in enumerate(ordered_eps):
        ep_trim = start_local if i == 0 else 0.0
        ordered = sorted(segs, key=lambda s: float(s.get("start_time") or 0.0))
        for s in ordered:
            seg_start = float(s.get("start_time") or 0.0)
            seg_end = min(float(s.get("end_time") or 0.0), ep.duration)  # clamp 到集时长
            if i == 0:
                if seg_end <= start_local:
                    continue
                seg_start = max(seg_start, start_local)
            if seg_end <= seg_start:
                continue
            out.append(TimedSegment(
                episode_no=ep.episode_no,
                seg_start=seg_start,
                seg_end=seg_end,
                cum_start=real_offset + (seg_start - ep_trim),
                cum_end=real_offset + (seg_end - ep_trim),
                copy=str(s.get("copy") or ""),
            ))
        real_offset += ep.duration - ep_trim
    return out


@dataclass(frozen=True)
class EndBoundary:
    cum_time: float
    episode_no: int
    local_end: float
    boundary_type: str  # 'sentence' | 'shot' | 'hard_cut'


def _is_sentence_safe(copy: str) -> bool:
    """该段结尾是否「说话安全」：无台词，或台词以句末标点结束。"""
    text = copy.strip()
    if not text:
        return True
    return text[-1] in SENTENCE_PUNCT


def pick_end_boundary(
    timeline: list[TimedSegment],
    window: tuple[float, float] = WINDOW,
    ideal: float = IDEAL,
) -> EndBoundary:
    """在 [window] 内选收尾边界：句子安全优先 → 分镜切点其次 → hard_cut 兜底。"""
    lo, hi = window
    in_window = [ts for ts in timeline if lo <= ts.cum_end <= hi]

    safe = [ts for ts in in_window if _is_sentence_safe(ts.copy)]
    if safe:
        # 在窗口内句子安全边界里，选 cum_end 与 ideal 绝对距离最近的
        best = min(safe, key=lambda ts: abs(ts.cum_end - ideal))
        return EndBoundary(best.cum_end, best.episode_no, best.seg_end, "sentence")

    if in_window:
        best = min(in_window, key=lambda ts: abs(ts.cum_end - ideal))
        return EndBoundary(best.cum_end, best.episode_no, best.seg_end, "shot")

    # 窗口内无任何 segment 边界（极端超长镜头）→ 在 ideal 处硬切
    for ts in timeline:
        if ts.cum_start <= ideal < ts.cum_end:
            local = ts.seg_start + (ideal - ts.cum_start)
            return EndBoundary(ideal, ts.episode_no, local, "hard_cut")
    # ideal 超出整条时间线（剧太短）→ 用最后一段末尾
    last = timeline[-1]
    return EndBoundary(last.cum_end, last.episode_no, last.seg_end, "hard_cut")


def resolve_real_end(
    start_local: float,
    episodes: list["EpisodeRef"],
    target_cum: float,
    boundary_type: str = "hard_cut",
) -> EndBoundary:
    """把切片累计时间 target_cum 映射回 (episode_no, 集内秒) 作为收尾点。

    两种用途：
    - 兜底硬切：窗口内找不到逻辑切点时，按真实集时长在 target_cum 处干净切
      （boundary_type 默认 'hard_cut'）。
    - 逻辑切点回填：span 细拆已在 [window] 内选好 cum_time + 边界类型，这里只负责把
      span 内的累计时间换算成逐集 (episode_no, 集内秒)，并透传 boundary_type。
    episodes 是从起点集起的有序 EpisodeRef。
    """
    remaining = target_cum
    for i, ep in enumerate(episodes):
        base = start_local if i == 0 else 0.0
        avail = ep.duration - base  # 该集相对切片可用的真实时长
        if remaining <= avail:
            return EndBoundary(
                cum_time=target_cum,
                episode_no=ep.episode_no,
                local_end=base + remaining,
                boundary_type=boundary_type,
            )
        remaining -= avail
    # target 超出全部可用真实时长 → 落最后一集末尾
    last = episodes[-1]
    return EndBoundary(
        cum_time=target_cum - remaining,
        episode_no=last.episode_no,
        local_end=last.duration,
        boundary_type=boundary_type,
    )


def timeline_from_shots(shots: list[dict]) -> list[TimedSegment]:
    """把一段连续 span（从切片起点起的真实原片）的细拆分镜转成 TimedSegment。

    span 的分镜时间戳本就相对 span 起点（= 切片起点 = 0），故 cum 直接等于段内时间。
    episode_no 用占位 0：span 内不区分集，收尾的逐集映射由 resolve_real_end 用真实集
    时长完成。用于在 span 上跑 pick_end_boundary 选逻辑收尾切点。
    """
    out: list[TimedSegment] = []
    for s in sorted(shots, key=lambda x: float(x.get("start_time") or 0.0)):
        st = float(s.get("start_time") or 0.0)
        en = float(s.get("end_time") or 0.0)
        if en <= st:
            continue
        out.append(TimedSegment(
            episode_no=0, seg_start=st, seg_end=en,
            cum_start=st, cum_end=en, copy=str(s.get("copy") or ""),
        ))
    return out


@dataclass(frozen=True)
class ClipEntry:
    asset_id: int
    episode_no: int
    cut_start: float
    cut_end: float
    oss_key: str


@dataclass(frozen=True)
class ClipPlan:
    entries: tuple[ClipEntry, ...]
    total_duration: float  # timeline 上的累计时长 (= end.cum_time)，不一定等于各 entry (cut_end - cut_start) 之和
    boundary_type: str
    start_episode_no: int
    start_local: float


def build_clip_plan(
    start: StartCandidate,
    end: EndBoundary,
    episodes: list["EpisodeRef"],
) -> ClipPlan:
    """把 (起点, 收尾边界) 反算成按 episode_no 顺序的逐集裁切计划。

    episodes 必须是从起点集到收尾集（含）的有序 EpisodeRef 列表。
    """
    entries: list[ClipEntry] = []
    for ep in episodes:
        if ep.episode_no < start.episode_no or ep.episode_no > end.episode_no:
            continue
        is_start = ep.episode_no == start.episode_no
        is_end = ep.episode_no == end.episode_no
        cut_start = start.local_start if is_start else 0.0
        cut_end = end.local_end if is_end else ep.duration
        if cut_end <= cut_start:
            continue
        entries.append(ClipEntry(
            asset_id=ep.asset_id, episode_no=ep.episode_no,
            cut_start=cut_start, cut_end=cut_end, oss_key=ep.oss_key,
        ))
    return ClipPlan(
        entries=tuple(entries),
        total_duration=end.cum_time,
        boundary_type=end.boundary_type,
        start_episode_no=start.episode_no,
        start_local=start.local_start,
    )
