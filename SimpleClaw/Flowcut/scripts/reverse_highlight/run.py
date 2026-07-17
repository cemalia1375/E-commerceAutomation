"""逆向分析：跑量切片是怎么从原片选段 + 重排出来的。

用法（在 SimpleClaw/ 下）：
    uv run python -m Flowcut.scripts.reverse_highlight.run

把「跑量切片」与「原片前 N 集」分别用 gemini_video.analyze_video 拆镜（台词逐字转录），
再把切片每个段落按台词定位回原片，算出钩子前置、跨集分布、重排、删减等指标，
落地为每部剧一份 markdown 报告 + 机读 alignment.json，外加一张跨剧对比总表。

产物供「高光提取 prompt」设计参考——人工剪的跑量切片即已验证的标准答案。
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path

# 必须在 import gemini_video 之前设置：长切片拆镜需要更大的输出和更长超时。
os.environ.setdefault("FLOWCUT_GEMINI_MAX_OUTPUT_TOKENS", "32768")
os.environ.setdefault("FLOWCUT_GEMINI_TIMEOUT_S", "600")

from dotenv import load_dotenv

from Flowcut.services import gemini_video
from Flowcut.scripts.reverse_highlight import align

load_dotenv()

_HERE = Path(__file__).resolve().parent
WORKDIR = _HERE / "workdir"
OUTPUT = _HERE / "output"
N_EPISODES = 6
AD_TIMEOUT_S = 600.0
EP_CONCURRENCY = 3

_DEFAULT_BASE = Path.home() / "Downloads" / "近期跑量+原剧"
AD_DIR = Path(os.getenv("REVERSE_AD_DIR", str(_DEFAULT_BASE / "近期跑量视频")))
ORIG_DIR = Path(os.getenv("REVERSE_ORIG_DIR", str(_DEFAULT_BASE / "原剧")))


# (slug, 垂类, 画风, 跑量切片文件名, 原片子目录名)
DRAMAS = [
    ("重卡封神", "男频·都市行业逆袭", "真人写实",
     "重卡封神：我靠规矩翻盘人生.mp4", "重卡封神：我靠规矩翻盘人生"),
    ("盲眼御兽师", "男频·玄幻御兽", "3D-CG+真人混剪",
     "盲眼御兽师：我的灵宠全是上古神.mp4", "盲眼御兽师：我的灵宠全是上古神"),
    ("蒙蒙的回头草", "都市·职场情感逆袭", "真人写实",
     "蒙蒙的回头草.mp4", "蒙蒙的回头草"),
]


def _mmss(sec: float | None) -> str:
    if sec is None:
        return "—"
    sec = max(0.0, float(sec))
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def _seg_total_duration(segs: list[dict]) -> float:
    if not segs:
        return 0.0
    return max(float(s.get("end_time") or 0.0) for s in segs)


async def _decompose_cached(video_path: Path, cache_path: Path, *, timeout_s: float | None = None) -> list[dict]:
    """拆镜带缓存：cache 命中且非空则直接读；否则调 Gemini 并落盘。"""
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, list) and cached:
                print(f"    [cache] {cache_path.name} ({len(cached)} 段)")
                return cached
        except Exception:
            pass
    if not video_path.exists():
        print(f"    [skip] 文件不存在: {video_path}")
        return []
    print(f"    [gemini] 拆镜 {video_path.name} ...")
    segs = await gemini_video.analyze_video(str(video_path), timeout_s=timeout_s)
    if not segs:
        # 空返回多为偶发（限流/截断），不落缓存，下次自动重试
        print(f"    [gemini] {video_path.name} → 0 段（不缓存，留待重试）")
        return segs
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(segs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    [gemini] {video_path.name} → {len(segs)} 段，已缓存")
    return segs


async def _decompose_episodes(orig_subdir: str, drama_workdir: Path) -> dict[int, list[dict]]:
    """并发拆镜原片前 N 集，返回 {episode_no: segments}。"""
    sem = asyncio.Semaphore(EP_CONCURRENCY)

    async def one(ep_no: int) -> tuple[int, list[dict]]:
        async with sem:
            vid = ORIG_DIR / orig_subdir / f"第{ep_no}集.mp4"
            cache = drama_workdir / f"ep{ep_no}.json"
            segs = await _decompose_cached(vid, cache)
            return ep_no, segs

    results = await asyncio.gather(*(one(n) for n in range(1, N_EPISODES + 1)))
    return {ep: segs for ep, segs in results if segs}


async def analyze_drama(slug: str, vertical: str, art_style: str,
                        ad_file: str, orig_subdir: str) -> dict:
    print(f"\n=== {slug}（{vertical} / {art_style}）===")
    drama_workdir = WORKDIR / slug
    drama_workdir.mkdir(parents=True, exist_ok=True)

    ad_segs = await _decompose_cached(
        AD_DIR / ad_file, drama_workdir / "ad.json", timeout_s=AD_TIMEOUT_S
    )
    per_ep = await _decompose_episodes(orig_subdir, drama_workdir)

    origins = align.to_origin_segments(per_ep)
    ad_total = _seg_total_duration(ad_segs)
    alignment = align.build_alignment(ad_segs, origins)
    metrics = align.reorder_metrics(alignment, origins, ad_total)

    result = {
        "slug": slug, "vertical": vertical, "art_style": art_style,
        "ad_file": ad_file, "ad_total_duration": ad_total,
        "ad_segment_count": len(ad_segs),
        "origin_episodes": sorted(per_ep.keys()),
        "origin_segment_count": len(origins),
        "metrics": asdict(metrics),
        "alignment": [asdict(m) for m in alignment],
    }
    (OUTPUT / f"{slug}_alignment.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTPUT / f"{slug}_report.md").write_text(
        _render_report(result, per_ep), encoding="utf-8")
    print(f"  → 匹配 {metrics.matched_segments}/{metrics.total_segments} 段，"
          f"覆盖 {metrics.coverage_ratio:.0%}，跨集 {list(metrics.episodes_used)}，"
          f"钩子前置={metrics.hook_is_front_loaded}")
    return result


def _is_matched(mm: dict) -> bool:
    return mm.get("signal", "none") != "none"


def _render_report(r: dict, per_ep: dict[int, list[dict]]) -> str:
    m = r["metrics"]
    L: list[str] = []
    L.append(f"# 逆向高光分析 · {r['slug']}")
    L.append(f"\n**垂类**：{r['vertical']}　**画风**：{r['art_style']}　**切片**：{r['ad_file']}")
    L.append("\n## 一、概况")
    L.append(f"- 跑量切片：{_mmss(r['ad_total_duration'])}，拆出 {r['ad_segment_count']} 段")
    L.append(f"- 原片样本：前 {len(r['origin_episodes'])} 集（{r['origin_episodes']}），共 {r['origin_segment_count']} 段")
    L.append(f"- 段落匹配率：{m['matched_segments']}/{m['total_segments']}　时长覆盖率：{m['coverage_ratio']:.0%}")
    L.append(f"- 跨集范围：{list(m['episodes_used'])}　最早出现集：第{m['earliest_episode']}集")
    L.append(f"- **钩子是否前置**：{'是 ✅（开头用了更靠后的高光）' if m['hook_is_front_loaded'] else '否（基本按原片顺序开场）'}"
             f"　钩子取自：第{m['hook_episode']}集")
    L.append(f"- 顺序保真度：{m['order_fidelity']:.2f}（1=完全照原片顺序，0=完全打乱）")
    L.append(f"- 原片台词段被丢弃比例：{m['origin_drop_ratio']:.0%}（前{len(r['origin_episodes'])}集里没被切片选用的台词段占比）")

    L.append("\n## 二、开头钩子（切片前 3 段来源）")
    L.append("| 切片序 | 切片时间 | 来源 | 信号 | 相似度 | 台词 |")
    L.append("|---|---|---|---|---|---|")
    for mm in r["alignment"][:3]:
        src = f"第{mm['episode_no']}集 @{_mmss(mm['origin_start'])}" if _is_matched(mm) else "未匹配"
        L.append(f"| {mm['ad_idx']} | {_mmss(mm['ad_start'])} | {src} | {mm['signal']} "
                 f"| {mm['score']:.2f} | {(mm['ad_copy'] or '（无台词）')[:30]} |")

    L.append("\n## 三、各集贡献")
    L.append("| 原集 | 命中段数 | 贡献时长 |")
    L.append("|---|---|---|")
    by_ep: dict[int, list[dict]] = {}
    for mm in r["alignment"]:
        if _is_matched(mm):
            by_ep.setdefault(mm["episode_no"], []).append(mm)
    for ep in sorted(by_ep):
        dur = sum(x["ad_end"] - x["ad_start"] for x in by_ep[ep])
        L.append(f"| 第{ep}集 | {len(by_ep[ep])} | {dur:.0f}s |")

    L.append("\n## 四、重排明细（切片顺序 → 原片位置）")
    L.append("| 切片序 | 切片时间 | 原片位置 | 信号 | 相似度 | 台词 |")
    L.append("|---|---|---|---|---|---|")
    for mm in r["alignment"]:
        src = f"第{mm['episode_no']}集@{_mmss(mm['origin_start'])}" if _is_matched(mm) else "—未匹配—"
        L.append(f"| {mm['ad_idx']} | {_mmss(mm['ad_start'])} | {src} | {mm['signal']} "
                 f"| {mm['score']:.2f} | {(mm['ad_copy'] or '（无台词）')[:36]} |")

    unmatched = [mm for mm in r["alignment"] if not _is_matched(mm)]
    L.append(f"\n## 五、未匹配的切片段（{len(unmatched)} 段，疑似切片新增的引导/字幕/桥接）")
    for mm in unmatched:
        L.append(f"- [{mm['ad_idx']}] {_mmss(mm['ad_start'])}: {(mm['ad_copy'] or '（无台词）')[:40]}")
    return "\n".join(L) + "\n"


def _render_summary(results: list[dict]) -> str:
    L: list[str] = ["# 跨剧对比总表 · 逆向高光\n"]
    L.append("| 剧 | 垂类 | 画风 | 切片时长 | 匹配率 | 覆盖率 | 跨集 | 钩子前置 | 顺序保真 | 原片删弃 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        m = r["metrics"]
        L.append(
            f"| {r['slug']} | {r['vertical']} | {r['art_style']} | {_mmss(r['ad_total_duration'])} "
            f"| {m['matched_segments']}/{m['total_segments']} | {m['coverage_ratio']:.0%} "
            f"| {list(m['episodes_used'])} | {'是' if m['hook_is_front_loaded'] else '否'} "
            f"| {m['order_fidelity']:.2f} | {m['origin_drop_ratio']:.0%} |"
        )
    L.append("\n> 详见各剧 `<slug>_report.md`，机读数据 `<slug>_alignment.json`。")
    return "\n".join(L) + "\n"


async def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    results = []
    for d in DRAMAS:
        try:
            results.append(await analyze_drama(*d))
        except Exception as exc:  # 单部失败不阻断其余
            print(f"  [error] {d[0]} 失败: {exc!r}")
    if results:
        (OUTPUT / "_summary.md").write_text(_render_summary(results), encoding="utf-8")
        print(f"\n✅ 完成 {len(results)}/{len(DRAMAS)} 部，报告在 {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
