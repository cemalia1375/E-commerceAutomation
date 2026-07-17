# script/runner/memory_snapshot.py
"""终态快照：把最终记忆条目 + dream 操作渲染成人读的 Markdown。

数据由 runner 在 SCENARIO END 时查库提供；本模块只做纯渲染，便于单测。
"""
from __future__ import annotations

import json
from typing import Any

_ARTIFACT_CONTENT_CAP = 6000  # draft 正文渲染上限，避免快照文件过大


def _render_artifact_content(content: str) -> list[str]:
    """把 dream artifact 的 draft 正文（review JSON）渲染成缩进文本。

    能解析成 JSON 就 pretty-print；解析失败则按原文输出（截断保护）。
    """
    text = (content or "").strip()
    if not text:
        return ["  （draft 正文为空）"]
    try:
        parsed = json.loads(text)
        pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
    except (ValueError, TypeError):
        pretty = text
    if len(pretty) > _ARTIFACT_CONTENT_CAP:
        pretty = pretty[:_ARTIFACT_CONTENT_CAP] + "\n…（已截断，完整内容查 nb_subagent_artifacts.content）"
    return [f"  {line}" for line in pretty.splitlines()]


def render_memory_snapshot_md(
    *,
    scenario_id: str,
    tenant_key: str,
    entries: list[dict[str, Any]],
    ledgers: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> str:
    lines: list[str] = [f"# Memory — {scenario_id} (tenant={tenant_key})", ""]

    lines.append("## 1. 最终记忆条目 (nb_memory_entries)")
    lines.append("")
    if not entries:
        lines.append("（无记忆条目）")
    else:
        for e in entries:
            tag = "skin" if e.get("is_skin") else "—"
            lines.append(f"### [{tag}] topic={e.get('topic') or ''}  source={e.get('source') or ''}")
            lines.append(f"description: {e.get('description') or ''}")
            content = str(e.get("content") or "").strip()
            if content:
                lines.append("content:")
                for cl in content.splitlines():
                    lines.append(f"  {cl}")
            lines.append("")

    lines.append("## 2. Dream 操作")
    lines.append("")
    art_by_status = artifacts or []
    if not ledgers and not art_by_status:
        lines.append("（无 dream 操作）")
    else:
        for l in ledgers:
            lines.append(f"### ledger {l.get('ledger_id') or ''}")
            lines.append(f"  status={l.get('status') or ''}  dream_status={l.get('dream_status') or ''}")
            g = l.get("guardrail")
            if isinstance(g, dict):
                lines.append(
                    f"  guardrail: verdict={g.get('verdict')}  rejected={g.get('rejected')}  "
                    f"checked_lines={g.get('checked_lines')}"
                )
            else:
                lines.append("  guardrail: absent")
            lines.append("")
        for a in art_by_status:
            applied = "applied" if a.get("applied") else "draft"
            lines.append(
                f"- artifact {a.get('artifact_key') or ''}  status={a.get('status') or ''}  ({applied})"
            )
            lines.append("  <details> draft 正文：")
            lines.extend(_render_artifact_content(str(a.get("content") or "")))
            lines.append("")
        if not ledgers:
            # 仅有 artifact 无 ledger 时，上面的 for-ledger 不会输出标题，补一行可读性
            pass
    lines.append("")
    return "\n".join(lines)
