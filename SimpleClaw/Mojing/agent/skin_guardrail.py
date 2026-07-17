"""皮肤记忆数字护栏（纯函数）。

落库前校验 LLM 写的 skin 条目里的方向叙述是否与代码算的趋势矛盾。
矛盾则拒该行、回退到代码骨架（描述层），宁可少叙事也不写错数字（spec §4.3）。

严重度实为两档（轻/重），方向标签为：持续轻度 / 持续重度 / 先加重后改善 /
加重 / 改善 / 先改善后反复 / 仅一次记录 / 无有效记录。护栏按"叙述声称的方向家族"
（加重 / 改善 / 无变化）与实际趋势比对，仅在 LLM 的声称被所有实际趋势否定时才判矛盾
（保守：避免误杀，只拦明显矛盾）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from Mojing.agent.skin_trend import ConcernTrend, render_trend_facts, render_timeline

# 叙述方向家族 → 在 LLM 文本里命中该家族的关键词。
_CLAIM_TOKENS: dict[str, list[str]] = {
    "加重": ["越来越严重", "持续加重", "一直加重", "加重", "恶化", "加剧"],
    "改善": ["好转", "改善", "减轻", "缓解", "变好"],
    "无变化": ["无变化", "没变化", "保持稳定", "一直没变", "始终如一"],
}

# 家族声称 → 与之矛盾的实际方向集合（实际方向若全部落在此集合，说明无任一趋势支持该声称）。
_CLAIM_CONTRADICTED_BY: dict[str, set[str]] = {
    "加重": {"持续轻度", "改善"},
    "改善": {"持续重度", "加重"},
    "无变化": {"加重", "改善", "先加重后改善", "先改善后反复"},
}


@dataclass(frozen=True)
class GuardrailResult:
    ok: bool
    violations: list[str] = field(default_factory=list)
    skeleton_description: str = ""
    skeleton_timeline: str = ""


def verify_skin_memory(
    *,
    description: str,
    content: str,
    trends: list[ConcernTrend],
) -> GuardrailResult:
    """校验 description/content 的方向叙述与 trends 是否矛盾。"""
    if not trends:
        return GuardrailResult(ok=True)

    text = f"{description}\n{content}"
    actual_directions = {t.direction for t in trends}
    violations: list[str] = []

    for family, tokens in _CLAIM_TOKENS.items():
        if not any(tok in text for tok in tokens):
            continue
        contradicted_by = _CLAIM_CONTRADICTED_BY.get(family, set())
        # 仅当所有实际方向都否定该声称（没有任何一条趋势支持它）才判矛盾。
        if actual_directions and actual_directions <= contradicted_by:
            violations.append(
                f"叙述方向「{family}」与实际趋势 {sorted(actual_directions)} 矛盾"
            )

    if violations:
        return GuardrailResult(
            ok=False,
            violations=violations,
            skeleton_description=render_trend_facts(trends),
            skeleton_timeline=render_timeline(trends),
        )
    return GuardrailResult(ok=True)
