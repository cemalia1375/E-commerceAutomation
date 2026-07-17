"""皮肤趋势计算器（纯函数）。

输入 nb_tenant_skin_profiles 多行，输出每 (concern, region) 的 severity 序列、
方向、窗口、coverage，并渲染"趋势事实块"（喂 memory_extract prompt）与
"逐日时间线"（写入 memory content）。

设计约束（来自 spec D6 + 真实数据核实 2026-06-08）：
- 趋势数字（严重度/日期/方向）一律由代码算，LLM 不许直接出数。
- **严重度实为两档**：真实 signals_json 的 `severity` 只有 轻度/重度（无中度），
  且约 1/3 信号的 severity 为 null。映射 轻度→1、重度→2（中度若偶现亦折叠为 2，
  与 skin_profile_sync 展示层一致）；**无可识别严重度 → None，单独跳过，绝不当作 0**。
- 因数据只有两档，方向不做"连续曲线/峰值"拟合，改用端点 + 档位出现情况判定。
- 兼容两种 signals_json schema：常见 schema 用 signalCode/locationText；少数 schema
  用 code/regions(列表)。concern 取 signalCode|code，region 取 locationText|regions。
- business_date 按 04:00 边界（复用 skin_diary_time 的北京时区语义）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from Mojing.utils.skin_diary_time import to_beijing_time

# concern → 固定主题组。LLM 不许新造 topic；本表用于趋势事实块的分组标签。
_CONCERN_THEME: dict[str, str] = {
    "黑头": "毛孔与黑头", "粉刺": "毛孔与黑头", "毛孔": "毛孔与黑头", "闭口": "毛孔与黑头",
    "丘疹": "痘痘炎症", "脓疱": "痘痘炎症", "痘印": "痘痘炎症", "痤疮": "痘痘炎症",
    "泛红": "泛红敏感", "红血丝": "泛红敏感", "敏感": "泛红敏感",
    "色斑": "色斑暗沉", "暗沉": "色斑暗沉", "色沉": "色斑暗沉",
}

# 两档映射：中度在真实数据中不出现；若偶现，折叠入重度（与 skin_profile_sync 一致）。
_SEVERITY_TEXT_TO_LEVEL: dict[str, int] = {"轻度": 1, "中度": 2, "重度": 2}

_LEVEL_TEXT = {1: "轻度", 2: "重度"}


def severity_to_level(signal: dict[str, Any]) -> int | None:
    """两档映射：轻度→1、重度→2；无可识别严重度 → None（单独跳过，绝不当作 0）。

    severity 文本（中文档位）优先；severityLevel 仅作历史兼容回退（真实数据恒为 null）。
    """
    raw = str(signal.get("severity") or "").strip()
    if raw in _SEVERITY_TEXT_TO_LEVEL:
        return _SEVERITY_TEXT_TO_LEVEL[raw]
    lvl_raw = signal.get("severityLevel", signal.get("severity_level"))
    if lvl_raw in (None, ""):
        return None
    try:
        lvl = int(lvl_raw)
    except (TypeError, ValueError):
        return None
    if lvl >= 2:
        return 2
    if lvl == 1:
        return 1
    return None  # 0 或未知 → 视为无严重度（不是健康基线，单独跳过）


def concern_theme(concern: str) -> str:
    """concern → 固定主题组；未知 concern 兜底为"其他皮肤问题"。"""
    for key, theme in _CONCERN_THEME.items():
        if key in concern:
            return theme
    return "其他皮肤问题"


def business_date_of(moment: datetime) -> date:
    """按 04:00 边界取业务日：北京时间减 4 小时后取日期。"""
    beijing = to_beijing_time(moment)
    return (beijing - timedelta(hours=4)).date()


@dataclass(frozen=True)
class SeverityPoint:
    business_date: date
    level: int  # 1=轻度, 2=重度（两档）


@dataclass(frozen=True)
class ConcernTrend:
    concern: str
    region: str
    theme: str
    points: tuple[SeverityPoint, ...]
    direction: str
    window_start: date
    window_end: date
    coverage: float


def _signal_concern(signal: dict[str, Any]) -> str:
    """兼容双 schema：常见用 signalCode，少数用 code。"""
    return str(
        signal.get("signalCode")
        or signal.get("signal_code")
        or signal.get("code")
        or ""
    ).strip()


def _signal_regions(signal: dict[str, Any]) -> list[str]:
    """兼容双 schema：locationText（字符串，· 分隔）或 regions（列表）。"""
    loc = signal.get("locationText") or signal.get("location_text")
    if loc:
        parts = [p.strip() for p in str(loc).split("·") if p.strip()]
        return parts or ["全脸"]
    regions = signal.get("regions")
    if isinstance(regions, list):
        parts = [str(r).strip() for r in regions if str(r).strip()]
        return parts or ["全脸"]
    if regions:
        parts = [p.strip() for p in str(regions).split("·") if p.strip()]
        return parts or ["全脸"]
    return ["全脸"]


def _classify_direction(levels: list[int]) -> str:
    """两档（1=轻,2=重）方向判定：基于端点 + 档位出现情况，不做连续曲线拟合。"""
    if not levels:
        return "无有效记录"
    if len(levels) == 1:
        return "仅一次记录"
    distinct = set(levels)
    if distinct == {1}:
        return "持续轻度"
    if distinct == {2}:
        return "持续重度"
    # 同时出现轻与重
    first, last = levels[0], levels[-1]
    if first == 1 and last == 1:
        return "先加重后改善"   # 轻→（中途重）→轻
    if first == 1 and last == 2:
        return "加重"           # 轻→重
    if first == 2 and last == 1:
        return "改善"           # 重→轻
    return "先改善后反复"        # 重→（中途轻）→重


def compute_trends(profiles: list[dict[str, Any]]) -> list[ConcernTrend]:
    """聚合多行 profiles → 每 (concern, region) 一条趋势。

    profiles 顺序不限（内部按 business_date 排序）。同一业务日多行取该日最高严重度。
    无可识别严重度（severity 为 null）的信号整条跳过。
    """
    # (concern, region) -> {business_date: level}
    buckets: dict[tuple[str, str], dict[date, int]] = {}
    for profile in profiles:
        created = profile.get("created_at")
        if not isinstance(created, datetime):
            continue
        bdate = business_date_of(created)
        raw_signals = profile.get("signals_json") or []
        if isinstance(raw_signals, str):
            try:
                raw_signals = json.loads(raw_signals)
            except (json.JSONDecodeError, TypeError):
                raw_signals = []
        signals = raw_signals if isinstance(raw_signals, list) else []
        if not signals:
            continue
        for signal in signals:
            if not isinstance(signal, dict):
                continue
            concern = _signal_concern(signal)
            if not concern:
                continue
            level = severity_to_level(signal)
            if level is None:  # null 严重度单独跳过，不进趋势
                continue
            for region in _signal_regions(signal):
                key = (concern, region)
                day_map = buckets.setdefault(key, {})
                day_map[bdate] = max(day_map.get(bdate, 0), level)

    if not buckets:
        return []

    trends: list[ConcernTrend] = []
    for (concern, region), day_map in buckets.items():
        ordered_dates = sorted(day_map.keys())
        points = tuple(SeverityPoint(d, day_map[d]) for d in ordered_dates)
        levels = [p.level for p in points]
        cwindow_days = (ordered_dates[-1] - ordered_dates[0]).days + 1
        coverage = len(ordered_dates) / cwindow_days if cwindow_days > 0 else 0.0
        trends.append(ConcernTrend(
            concern=concern,
            region=region,
            theme=concern_theme(concern),
            points=points,
            direction=_classify_direction(levels),
            window_start=ordered_dates[0],
            window_end=ordered_dates[-1],
            coverage=coverage,
        ))
    # 稳定排序：主题 → concern → region
    trends.sort(key=lambda t: (t.theme, t.concern, t.region))
    return trends


def _fmt_date(d: date) -> str:
    return f"{d.month}.{d.day}"


def render_trend_facts(trends: list[ConcernTrend]) -> str:
    """渲染喂给 memory_extract prompt 的"趋势事实块"（代码供数，权威）。"""
    if not trends:
        return "（暂无可用皮肤画像，跳过皮肤趋势刷新）"
    lines = ["以下皮肤趋势事实由系统从历史画像精确算出，severity_timeline 的数字必须照抄，不得改动："]
    for t in trends:
        span = f"{_fmt_date(t.window_start)}-{_fmt_date(t.window_end)}"
        cov = f"{int(round(t.coverage * 100))}%"
        lines.append(
            f"- {t.concern}（{t.region}）{span} {t.direction}"
            f"（首{_LEVEL_TEXT[t.points[0].level]}/末{_LEVEL_TEXT[t.points[-1].level]}，覆盖{cov}）"
        )
    return "\n".join(lines)


def render_timeline(trends: list[ConcernTrend]) -> str:
    """渲染写入 memory content 的逐日时间线（代码维护，可整体重算）。"""
    if not trends:
        return ""
    blocks: list[str] = []
    for t in trends:
        head = f"{t.concern}（{t.region}）："
        rows = [f"  {_fmt_date(p.business_date)} {_LEVEL_TEXT[p.level]}" for p in t.points]
        blocks.append(head + "\n" + "\n".join(rows))
    return "\n\n".join(blocks)
