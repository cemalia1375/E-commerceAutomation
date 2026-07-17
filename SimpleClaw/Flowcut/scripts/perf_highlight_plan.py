"""高光规划性能测试脚本。

用法：
    cd SimpleClaw
    uv run python -m Flowcut.scripts.perf_highlight_plan <drama_name> \
        [--candidates N] [--tenant-key KEY]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv

load_dotenv()

from Flowcut.config import make_db_kwargs
from Flowcut.storage.database import Database
from Flowcut.storage.highlight_asset_repo import HighlightAssetRepository
from Flowcut.storage.creative_repo import CreativeRepository
from Flowcut.storage.oss_client import build_oss_client


class EpisodeTiming(TypedDict):
    episode_no: int
    download_s: float
    normalize_s: float


class StageA(TypedDict):
    elapsed_s: float
    per_episode: list[EpisodeTiming]
    ffmpeg_merge_s: float
    gemini_analyze_s: float
    detect_scene_cuts_s: float
    total_shots: int


class StageB(TypedDict):
    elapsed_s: float
    select_start_shots_s: float
    candidates_picked: int


class CandidateTiming(TypedDict):
    episode_no: int
    local_start: float
    download_new_episodes_s: float
    ffmpeg_cut_concat_s: float
    gemini_span_analyze_s: float
    detect_scene_cuts_s: float
    pick_end_boundary_s: float
    creative_repo_write_s: float
    compose_submit_stub_s: float
    creative_id: int | None


class StageC(TypedDict):
    elapsed_s: float
    candidates: list[CandidateTiming]


class PerfResult(TypedDict):
    drama_name: str
    tenant_key: str
    num_candidates_requested: int
    total_elapsed_s: float
    stage_A: StageA
    stage_B: StageB
    stage_C: StageC


def write_result(result: PerfResult, drama_name: str) -> str:
    ts = int(time.time())
    safe_name = drama_name.replace("/", "_").replace(" ", "_")[:40]
    filename = f"perf_highlight_{safe_name}_{ts}.json"
    Path(filename).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return filename


async def main() -> None:
    parser = argparse.ArgumentParser(description="高光规划性能测试")
    parser.add_argument("drama_name", help="AI 漫剧名称")
    parser.add_argument("--candidates", type=int, default=3, help="候选数量（默认 3）")
    parser.add_argument("--tenant-key", default="flowcut", help="租户 key（默认 flowcut）")
    args = parser.parse_args()

    db = Database(**make_db_kwargs())
    await db.connect()
    try:
        highlight_asset_repo = HighlightAssetRepository(db)
        creative_repo = CreativeRepository(db)
        oss_client = build_oss_client()

        result = await run_perf(
            drama_name=args.drama_name,
            num_candidates=args.candidates,
            tenant_key=args.tenant_key,
            highlight_asset_repo=highlight_asset_repo,
            creative_repo=creative_repo,
            oss_client=oss_client,
        )

        filename = write_result(result, args.drama_name)
        print(f"\n=== 测试完成 ===")
        print(f"总耗时：{result['total_elapsed_s']:.1f}s")
        print(f"Stage A：{result['stage_A']['elapsed_s']:.1f}s")
        print(f"Stage B：{result['stage_B']['elapsed_s']:.1f}s")
        print(f"Stage C：{result['stage_C']['elapsed_s']:.1f}s")
        print(f"结果已写入：{filename}")
    finally:
        await db.close()


async def run_perf(
    *,
    drama_name: str,
    num_candidates: int,
    tenant_key: str,
    highlight_asset_repo: HighlightAssetRepository,
    creative_repo: CreativeRepository,
    oss_client,
) -> PerfResult:
    import os
    import shutil
    import tempfile
    import uuid

    from Flowcut.services.gemini_video import analyze_video, select_start_shots
    from Flowcut.services.scene_align import detect_scene_cuts, align_timestamps
    from Flowcut.services.clip_planner import (
        START_SEARCH_EPISODES, DEFAULT_CANDIDATES, DEDUP_GAP_S,
        WINDOW, IDEAL, MAX_FORWARD_EPISODES,
        EpisodeRef, StartCandidate,
        match_drama_episodes, locate, build_clip_plan, pick_end_boundary,
        resolve_real_end, timeline_from_shots,
    )
    from Flowcut.runtime.executors import (
        _ffmpeg_normalize_clip, _ffmpeg_cut_clip, _ffmpeg_concat,
        _write_concat_list, _probe_duration_seconds,
    )

    _HIGHLIGHT_SPAN_PAD_S = 8.0

    loop = asyncio.get_running_loop()
    total_t0 = time.perf_counter()

    # 查剧集
    rows = await highlight_asset_repo.list_by_tenant(
        tenant_key, asset_type="episode_source", drama_name=drama_name, limit=500,
    )
    episodes = sorted(rows, key=lambda r: int(r.get("episode_no") or 0))
    if not episodes:
        all_rows = await highlight_asset_repo.list_by_tenant(
            tenant_key, asset_type="episode_source", limit=500,
        )
        episodes = match_drama_episodes(all_rows, drama_name)
    if not episodes:
        raise RuntimeError(f"没有找到「{drama_name}」的 episode_source")

    ep_index = {int(a["episode_no"] or 0): a for a in episodes}
    tmp_dir = tempfile.mkdtemp(prefix=f"perf_highlight_{uuid.uuid4().hex[:8]}_")
    norm_cache: dict[int, tuple[str, float]] = {}
    norm_locks: dict[int, asyncio.Lock] = {}

    async def ensure_episode(asset: dict) -> tuple[tuple[str, float], float, float]:
        aid = int(asset["id"])
        if aid in norm_cache:
            return norm_cache[aid], 0.0, 0.0
        lock = norm_locks.setdefault(aid, asyncio.Lock())
        async with lock:
            if aid in norm_cache:
                return norm_cache[aid], 0.0, 0.0
            raw = os.path.join(tmp_dir, f"ep_{aid}.mp4")
            t0 = time.perf_counter()
            await loop.run_in_executor(
                None, oss_client.download,
                str(asset.get("oss_key") or asset.get("oss_url") or ""), raw,
            )
            dl_s = time.perf_counter() - t0

            norm = os.path.join(tmp_dir, f"ep_{aid}_norm.mp4")
            t0 = time.perf_counter()
            await loop.run_in_executor(None, _ffmpeg_normalize_clip, raw, norm)
            norm_s = time.perf_counter() - t0

            dur = await loop.run_in_executor(None, _probe_duration_seconds, norm)
            norm_cache[aid] = (norm, float(dur))
            return norm_cache[aid], dl_s, norm_s

    try:
        # ── Stage A ────────────────────────────────────────────────────────
        stage_a_t0 = time.perf_counter()
        head = episodes[:START_SEARCH_EPISODES]
        norm_paths: list[str] = []
        offsets: list[tuple[int, float]] = []
        durations: dict[int, float] = {}
        per_episode: list[EpisodeTiming] = []
        cum = 0.0

        for asset in head:
            ep_no = int(asset["episode_no"] or 0)
            (norm, dur), dl_s, norm_s = await ensure_episode(asset)
            norm_paths.append(norm)
            offsets.append((ep_no, cum))
            durations[ep_no] = dur
            cum += dur
            per_episode.append({"episode_no": ep_no, "download_s": round(dl_s, 2),
                                 "normalize_s": round(norm_s, 2)})
            print(f"  ep{ep_no}: download={dl_s:.1f}s normalize={norm_s:.1f}s")

        merged_path = os.path.join(tmp_dir, "head_merged.mp4")
        concat_list = os.path.join(tmp_dir, "head_concat.txt")
        _write_concat_list(concat_list, norm_paths)
        merge_t0 = time.perf_counter()
        await loop.run_in_executor(None, _ffmpeg_concat, concat_list, merged_path)
        ffmpeg_merge_s = time.perf_counter() - merge_t0
        print(f"  merge={ffmpeg_merge_s:.1f}s")

        cuts_task = asyncio.create_task(detect_scene_cuts(merged_path))
        gemini_t0 = time.perf_counter()
        raw_shots = await analyze_video(merged_path)
        gemini_analyze_s = time.perf_counter() - gemini_t0
        merged_cuts = await cuts_task
        detect_scene_s = time.perf_counter() - gemini_t0 - gemini_analyze_s
        print(f"  gemini={gemini_analyze_s:.1f}s detect_cuts={detect_scene_s:.1f}s shots={len(raw_shots or [])}")

        head_shots = align_timestamps(list(raw_shots), merged_cuts) if raw_shots else []
        if not head_shots:
            raise RuntimeError(f"「{drama_name}」前{len(head)}集拆镜为空")

        stage_a_elapsed = time.perf_counter() - stage_a_t0
        stage_a: StageA = {
            "elapsed_s": round(stage_a_elapsed, 2),
            "per_episode": per_episode,
            "ffmpeg_merge_s": round(ffmpeg_merge_s, 2),
            "gemini_analyze_s": round(gemini_analyze_s, 2),
            "detect_scene_cuts_s": round(detect_scene_s, 2),
            "total_shots": len(head_shots),
        }

        # ── Stage B ────────────────────────────────────────────────────────
        stage_b_t0 = time.perf_counter()
        picks = await select_start_shots(head_shots, top_n=num_candidates)
        select_s = time.perf_counter() - stage_b_t0

        candidates: list[StartCandidate] = []
        seen: list[float] = []
        for p in picks:
            shot = head_shots[p["idx"]]
            g = float(shot.get("start_time") or 0.0)
            if any(abs(g - x) < DEDUP_GAP_S for x in seen):
                continue
            loc = locate(g, offsets, durations)
            if loc is None:
                continue
            ep_no, local = loc
            seen.append(g)
            candidates.append(StartCandidate(
                episode_no=ep_no, local_start=local, global_start=g,
                hook_strength=float(p.get("hook_strength") or 0.0),
                reason=str(p.get("reason") or ""),
            ))

        stage_b_elapsed = time.perf_counter() - stage_b_t0
        stage_b: StageB = {
            "elapsed_s": round(stage_b_elapsed, 2),
            "select_start_shots_s": round(select_s, 2),
            "candidates_picked": len(candidates),
        }
        print(f"Stage B: select={select_s:.1f}s candidates={len(candidates)}")

        if not candidates:
            raise RuntimeError(f"「{drama_name}」未选出可用高光起点")

        # ── Stage C ────────────────────────────────────────────────────────
        stage_c_t0 = time.perf_counter()
        target_len = WINDOW[1] + _HIGHLIGHT_SPAN_PAD_S

        async def process_candidate(cand_idx: int, cand: StartCandidate) -> CandidateTiming | None:
            ep_refs: list[EpisodeRef] = []
            seg_specs: list[tuple[str, float, float]] = []
            acc = 0.0
            ep_no = cand.episode_no
            steps = 0

            # 下载本候选用到的新集（已有的从 norm_cache 直接取）
            dl_new_t0 = time.perf_counter()
            while ep_no in ep_index and steps < MAX_FORWARD_EPISODES:
                asset = ep_index[ep_no]
                aid = int(asset["id"])
                if aid not in norm_cache:
                    (norm, dur), _, _ = await ensure_episode(asset)  # type: ignore
                else:
                    norm, dur = norm_cache[aid]
                base = cand.local_start if ep_no == cand.episode_no else 0.0
                avail = dur - base
                if avail <= 0:
                    break
                take = min(avail, target_len - acc)
                seg_specs.append((norm, base, base + take))
                ep_refs.append(EpisodeRef(
                    asset_id=aid, episode_no=ep_no,
                    oss_key=str(asset.get("oss_key") or asset.get("oss_url") or ""),
                    duration=dur,
                ))
                acc += take
                steps += 1
                if acc >= target_len:
                    break
                ep_no += 1
            download_new_s = time.perf_counter() - dl_new_t0

            capacity = acc
            if capacity < WINDOW[0]:
                print(f"  候选 ep{cand.episode_no}: 可用 {capacity:.1f}s 不足，跳过")
                return None

            # ffmpeg cut + concat span
            cut_t0 = time.perf_counter()
            span_cut_paths: list[str] = []
            for i, (src, cs, ce) in enumerate(seg_specs):
                out = os.path.join(tmp_dir, f"span_{cand_idx}_{cand.episode_no}_{i}.mp4")
                await loop.run_in_executor(None, _ffmpeg_cut_clip, src, out, cs, ce)
                span_cut_paths.append(out)
            if len(span_cut_paths) == 1:
                span_path = span_cut_paths[0]
            else:
                span_path = os.path.join(tmp_dir, f"span_{cand_idx}_{cand.episode_no}.mp4")
                sl = os.path.join(tmp_dir, f"span_{cand_idx}_{cand.episode_no}_concat.txt")
                _write_concat_list(sl, span_cut_paths)
                await loop.run_in_executor(None, _ffmpeg_concat, sl, span_path)
            ffmpeg_cut_s = time.perf_counter() - cut_t0

            # Gemini span 细拆 + detect_scene_cuts（并行）
            span_cuts_task = asyncio.create_task(detect_scene_cuts(span_path))
            gemini_span_t0 = time.perf_counter()
            span_raw = await analyze_video(span_path)
            gemini_span_s = time.perf_counter() - gemini_span_t0
            span_phys = await span_cuts_task
            detect_span_s = time.perf_counter() - gemini_span_t0 - gemini_span_s
            span_shots = align_timestamps(list(span_raw), span_phys) if span_raw else []

            # pick_end_boundary
            pick_t0 = time.perf_counter()
            timeline = timeline_from_shots(span_shots)
            if timeline:
                eb = pick_end_boundary(timeline)
                end = resolve_real_end(
                    cand.local_start, ep_refs, eb.cum_time,
                    boundary_type=eb.boundary_type,
                )
            else:
                end = resolve_real_end(
                    cand.local_start, ep_refs, min(IDEAL, capacity),
                )
            plan = build_clip_plan(cand, end, ep_refs)
            pick_s = time.perf_counter() - pick_t0

            if not plan.entries:
                return None

            # DB 写入
            clip_plan_dict = {
                "drama_name": drama_name,
                "boundary_type": plan.boundary_type,
                "total_duration": plan.total_duration,
                "start_episode_no": plan.start_episode_no,
                "start_local": plan.start_local,
                "entries": [
                    {"asset_id": e.asset_id, "episode_no": e.episode_no,
                     "cut_start": e.cut_start, "cut_end": e.cut_end,
                     "oss_key": e.oss_key}
                    for e in plan.entries
                ],
            }
            db_t0 = time.perf_counter()
            creative = await creative_repo.create_cross_episode_job(
                tenant_key=tenant_key,
                session_key="perf_test",
                script_id=None,
                batch_id=uuid.uuid4().hex,
                source_asset_id=ep_index[cand.episode_no]["id"],
                clip_plan_json=json.dumps(clip_plan_dict, ensure_ascii=False),
                highlight_start=cand.local_start,
                highlight_reason_json=json.dumps(
                    {"hook_strength": cand.hook_strength, "reason": cand.reason},
                    ensure_ascii=False,
                ),
                connector_asset_id=None,
            )
            creative_id = int(creative["id"])
            db_s = time.perf_counter() - db_t0

            # compose 提交：只记录耗时，不实际入队（避免触发 worker 流程）
            compose_t0 = time.perf_counter()
            # stub：实际 submit 一行，这里只 sleep(0) 保持结构
            await asyncio.sleep(0)
            compose_s = time.perf_counter() - compose_t0

            ct: CandidateTiming = {
                "episode_no": cand.episode_no,
                "local_start": round(cand.local_start, 2),
                "download_new_episodes_s": round(download_new_s, 2),
                "ffmpeg_cut_concat_s": round(ffmpeg_cut_s, 2),
                "gemini_span_analyze_s": round(gemini_span_s, 2),
                "detect_scene_cuts_s": round(detect_span_s, 2),
                "pick_end_boundary_s": round(pick_s, 2),
                "creative_repo_write_s": round(db_s, 2),
                "compose_submit_stub_s": round(compose_s, 4),
                "creative_id": creative_id,
            }
            print(
                f"  候选 ep{cand.episode_no}: dl_new={download_new_s:.1f}s "
                f"cut={ffmpeg_cut_s:.1f}s gemini={gemini_span_s:.1f}s "
                f"pick={pick_s:.2f}s db={db_s:.2f}s creative_id={creative_id}"
            )
            return ct

        results = await asyncio.gather(
            *(process_candidate(i, c) for i, c in enumerate(candidates))
        )
        candidate_timings: list[CandidateTiming] = [ct for ct in results if ct is not None]

        stage_c_elapsed = time.perf_counter() - stage_c_t0
        stage_c: StageC = {
            "elapsed_s": round(stage_c_elapsed, 2),
            "candidates": candidate_timings,
        }

        total_elapsed = time.perf_counter() - total_t0
        return PerfResult(
            drama_name=drama_name,
            tenant_key=tenant_key,
            num_candidates_requested=num_candidates,
            total_elapsed_s=round(total_elapsed, 2),
            stage_A=stage_a,
            stage_B=stage_b,
            stage_C=stage_c,
        )

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
