"""highlight_plan 任务耗时基线收集脚本。

从 nb_runtime_tasks 拉取所有已完成的 highlight_plan 任务，
解析 result_details_json 中的 timings 数据，输出各阶段
p50 / p95 / p99 耗时报告。

用法:
    cd SimpleClaw
    uv run python -m Flowcut.scripts.perf_highlight_baseline
    uv run python -m Flowcut.scripts.perf_highlight_baseline --limit 20

输出示例:
    Stage                     p50       p95       p99    样本数
    ─────────────────────────────────────────────────────────
    stage_a_download_ffmpeg   89.2s    210.5s    287.3s     12
    stage_a_gemini_analyze    45.7s     98.3s    142.1s     12
    stage_a_total            134.9s    308.8s    429.4s     12
    stage_b_gemini_select      8.3s     15.2s     18.9s     11
    stage_c_total            187.5s    412.0s    521.7s     10
    wall_clock               330.7s    720.5s    890.2s     12
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

# 确保 SimpleClaw 在 path 中
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from Flowcut.storage.database import Database
from Flowcut.config import make_db_kwargs


def _flatten_timings(
    results: list[dict],
    wall_clock_s: float | None,
    drama_count: int,
) -> list[dict]:
    """将单次任务的多剧 timings 打平为每条一行。"""
    rows: list[dict] = []
    for r in results:
        t = r.get("timings") or {}
        row = {
            "drama": r.get("drama_name", "?"),
            "created_count": len(r.get("created") or []),
            "stage_a_download_ffmpeg_s": t.get("stage_a_download_ffmpeg_s"),
            "stage_a_gemini_analyze_s": t.get("stage_a_gemini_analyze_s"),
            "stage_a_total_s": t.get("stage_a_total_s"),
            "stage_b_gemini_select_s": t.get("stage_b_gemini_select_s"),
            "stage_c_total_s": t.get("stage_c_total_s"),
            "stage_c_candidates": t.get("stage_c_candidates"),
            "total_s": t.get("total_s"),
        }
        rows.append(row)
    # 顶层 wall_clock 作为独立行追加
    if wall_clock_s is not None:
        for row in rows:
            row["wall_clock_s"] = wall_clock_s
            row["parallel_dramas"] = drama_count
    return rows


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0, "p95": 0, "p99": 0}
    s = sorted(values)
    n = len(s)

    def _p(pct: float) -> float:
        k = (n - 1) * pct / 100.0
        lo = int(k)
        hi = min(lo + 1, n - 1)
        frac = k - lo
        return round(s[lo] + frac * (s[hi] - s[lo]), 2)

    return {"p50": _p(50), "p95": _p(95), "p99": _p(99)}


_FIELDS = [
    "stage_a_download_ffmpeg_s",
    "stage_a_gemini_analyze_s",
    "stage_a_total_s",
    "stage_b_gemini_select_s",
    "stage_c_total_s",
    "total_s",
    "wall_clock_s",
]

_LABELS = {
    "stage_a_download_ffmpeg_s": "OSS下载+ffmpeg合并",
    "stage_a_gemini_analyze_s": "Gemini 轻量拆镜",
    "stage_a_total_s": "Stage A 合计",
    "stage_b_gemini_select_s": "Gemini 挑高光起点",
    "stage_c_total_s": "Stage C 逐候选细拆",
    "total_s": "单剧总耗时",
    "wall_clock_s": "任务墙钟时间",
}


async def main(limit: int = 50) -> None:
    db = Database(**make_db_kwargs())
    await db.connect()

    try:
        async with db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT task_id, status, payload_json, result_details_json,
                           created_at, completed_at
                    FROM nb_runtime_tasks
                    WHERE task_type = 'highlight_plan'
                      AND status IN ('succeeded', 'failed')
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]
    finally:
        await db.close()

    if not rows:
        print("没有找到任何 highlight_plan 任务记录。触发一次跨集高光后再运行此脚本。")
        return

    all_timings: list[dict] = []
    skipped = 0

    for row in rows:
        item = dict(zip(cols, row))
        details_raw = item.get("result_details_json")
        if not details_raw:
            skipped += 1
            continue
        try:
            details = json.loads(details_raw) if isinstance(details_raw, str) else details_raw
        except (json.JSONDecodeError, TypeError):
            skipped += 1
            continue

        results = details.get("results") or []
        wc = details.get("wall_clock_s")
        dc = details.get("parallel_dramas") or details.get("drama_count") or 1
        # 兼容老数据：results 为空但 details 直接包含单剧 timings
        if not results and details.get("timings"):
            results = [{"drama_name": details.get("drama_name", "?"), "timings": details["timings"]}]
        if not results:
            skipped += 1
            continue

        flat = _flatten_timings(results, wc, int(dc))
        all_timings.extend(flat)

    if not all_timings:
        print(f"找到了 {len(rows)} 条任务，但 {skipped} 条无 timings 数据。")
        print("Phase 1 的计时埋点部署之后产生的任务才会有 timings。")
        print("请触发一次跨集高光，等完成后重新运行此脚本。")
        return

    # ── 输出报告 ──
    print(f"\n{'='*65}")
    print(f"  FlowCut highlight_plan 耗时基线报告")
    print(f"  样本: {len(all_timings)} 个剧次, {len(rows)} 次任务")
    print(f"  生成时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'='*65}\n")

    has_any = any(
        r.get(f) is not None for f in _FIELDS for r in all_timings
    )
    if not has_any:
        print("  (暂无分阶段耗时数据 — Phase 1 计时埋点部署后产生的新任务才会有)\n")
        return

    print(f"  {'阶段':<28} {'p50':>8} {'p95':>8} {'p99':>8} {'样本':>6}")
    print(f"  {'─'*28} {'─'*8} {'─'*8} {'─'*8} {'─'*6}")

    for field in _FIELDS:
        values = [r[field] for r in all_timings if r.get(field) is not None]
        if not values:
            continue
        p = _percentiles(values)
        label = _LABELS.get(field, field)
        print(f"  {label:<28} {p['p50']:>7.1f}s {p['p95']:>7.1f}s {p['p99']:>7.1f}s {len(values):>5} ")

    # 附加: 并行效率
    wall_values = [r["wall_clock_s"] for r in all_timings if r.get("wall_clock_s") is not None]
    total_values = [r["total_s"] for r in all_timings if r.get("total_s") is not None]
    if wall_values and total_values:
        avg_wall = statistics.mean(wall_values)
        avg_total = statistics.mean(total_values)
        drama_counts = [r.get("parallel_dramas", 1) for r in all_timings]
        avg_dramas = statistics.mean(drama_counts) if drama_counts else 1
        speedup = avg_total * avg_dramas / avg_wall if avg_wall > 0 else 1
        print(f"\n  并行效率: 平均 {avg_dramas:.1f} 剧并行")
        print(f"    串行预估: {avg_total * avg_dramas:.0f}s  →  实际墙钟: {avg_wall:.0f}s")
        print(f"    加速比:   {speedup:.1f}x")

    # 检查是否有不合理的热点
    a_ffmpeg = [r["stage_a_download_ffmpeg_s"] for r in all_timings if r.get("stage_a_download_ffmpeg_s") is not None]
    if a_ffmpeg:
        p95_ffmpeg = _percentiles(a_ffmpeg)["p95"]
        if p95_ffmpeg > 180:
            print(f"\n  ⚠ Stage A ffmpeg p95={p95_ffmpeg:.0f}s > 3min — 考虑降低归一化分辨率或码率")
        if p95_ffmpeg > 360:
            print(f"  🔴 Stage A ffmpeg p95={p95_ffmpeg:.0f}s > 6min — 视频文件可能过大，建议预处理")

    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="highlight_plan 耗时基线统计")
    p.add_argument("--limit", type=int, default=50, help="最多拉取的任务数 (default: 50)")
    args = p.parse_args()
    asyncio.run(main(limit=args.limit))
