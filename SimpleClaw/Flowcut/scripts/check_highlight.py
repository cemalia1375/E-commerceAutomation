"""跨集高光产出核查：查 nb_runtime_tasks + fc_creative + OSS，判断一次任务是否真成功。

用法（在 SimpleClaw 目录下）：
    uv run python -m Flowcut.scripts.check_highlight                 # 看最近 8 条成片 + 最近 6 个任务
    uv run python -m Flowcut.scripts.check_highlight "被儿媳逼相亲"   # 按剧名过滤

判定口径：
  - 规划成功 = 该 batch 在 nb_runtime_tasks 里 status='succeeded'，且 fc_creative 出现对应行
    （status=PENDING、有 clip_plan_json）。此时还没有视频，oss_key 为空属正常。
  - 合成成功 = fc_creative.status='READY' 且 oss_key 对应的对象在 OSS 真实存在。
"""
from __future__ import annotations

import asyncio
import os
import sys

import pymysql
from dotenv import load_dotenv

from Flowcut.storage.oss_client import build_oss_client


def _conn():
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"], port=int(os.environ.get("MYSQL_PORT", 3306)),
        user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
        db=os.environ["MYSQL_DB"], charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def main() -> None:
    load_dotenv()
    drama = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    oss = build_oss_client()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            print("=== 最近的 highlight_plan 任务 ===")
            cur.execute(
                """SELECT status, last_error, created_at,
                          JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.drama_name')) drama,
                          JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.batch_id')) batch,
                          JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.num_candidates')) num
                   FROM nb_runtime_tasks WHERE task_type='highlight_plan'
                   ORDER BY created_at DESC LIMIT 6"""
            )
            for r in cur.fetchall():
                if drama and drama not in (r["drama"] or ""):
                    continue
                err = f" 错误={r['last_error'][:60]}" if r["last_error"] else ""
                print(f"  [{r['status']}] {r['created_at']} drama={r['drama']!r} "
                      f"num={r['num']} batch={str(r['batch'])[:12]}{err}")

            print("\n=== 跨集高光成片 (fc_creative) + OSS 核验 ===")
            sql = """SELECT c.id, c.status, c.batch_id, c.oss_key,
                            LENGTH(c.clip_plan_json) plan_len, c.created_at,
                            src.drama_name drama
                     FROM fc_creative c
                     LEFT JOIN fc_highlight_asset src ON src.id = c.source_asset_id
                     WHERE c.creative_type='continuous_cross_episode'"""
            params: list = []
            if drama:
                sql += " AND src.drama_name LIKE %s"
                params.append(f"%{drama}%")
            sql += " ORDER BY c.id DESC LIMIT 8"
            cur.execute(sql, tuple(params))
            for r in cur.fetchall():
                oss_state = "—"
                if r["oss_key"]:
                    try:
                        oss_state = "OSS存在✓" if oss.object_exists(r["oss_key"]) else "OSS缺失✗"
                    except Exception as e:  # noqa: BLE001
                        oss_state = f"OSS查询失败({type(e).__name__})"
                stage = ("待合成(规划已成)" if r["status"] == "PENDING"
                         else "合成中" if r["status"] == "PROCESSING"
                         else "已合成" if r["status"] == "READY"
                         else r["status"])
                print(f"  id={r['id']} [{stage}] drama={r['drama']!r} plan={r['plan_len']}B "
                      f"oss_key={str(r['oss_key'])[:46]} {oss_state}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
