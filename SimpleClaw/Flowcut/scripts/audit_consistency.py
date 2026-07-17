"""三方存储一致性体检脚本 — MySQL ↔ OSS ↔ Qdrant。

以 MySQL 为真源，扫描 fc_material / fc_reference_video，校验：
  • OSS 对象是否仍存在（oss_key / audio_oss_key）
  • Qdrant 向量是否仍存在（fc_material.vector_indexed=1 → 应有 point；description 非空且 READY → 应能补向量）
  • Qdrant 是否存在孤儿 point（id 在 MySQL fc_material 已删）

用法：
    cd SimpleClaw
    # 只体检，不动数据
    uv run python -m Flowcut.scripts.audit_consistency

    # 自动修复：
    #   - missing-vector : 缺向量但 description 非空的 fc_material，inline 跑 embedding+upsert
    #   - missing-oss    : OSS 对象不存在的 fc_material/fc_reference_video，DB 行置 FAILED
    #   - orphan-vector  : Qdrant 多余 point，删除
    uv run python -m Flowcut.scripts.audit_consistency --fix

可单独打开某项修复：
    --fix-missing-vectors --fix-missing-oss --fix-orphan-vectors
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field

from dotenv import load_dotenv

from Flowcut.config import make_db_kwargs, make_embedding_config, make_qdrant_url
from Flowcut.services.embedding import EmbeddingService, build_embedding_service
from Flowcut.storage.database import Database
from Flowcut.storage.material_repo import MaterialRepository
from Flowcut.storage.oss_client import build_oss_client
from Flowcut.storage.reference_video_repo import ReferenceVideoRepository
from Flowcut.storage.vector_store import VectorStore

load_dotenv()

logger = logging.getLogger("audit_consistency")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


@dataclass(frozen=True)
class MaterialIssue:
    material_id: int
    tenant_key: str
    kind: str  # "missing_oss" | "missing_vector" | "stale_vector_flag"
    detail: str


@dataclass(frozen=True)
class RefVideoIssue:
    ref_video_id: int
    tenant_key: str
    kind: str  # "missing_oss_main" | "missing_oss_audio"
    detail: str


@dataclass(frozen=True)
class OrphanVector:
    point_id: int


@dataclass
class AuditReport:
    material_issues: list[MaterialIssue] = field(default_factory=list)
    ref_video_issues: list[RefVideoIssue] = field(default_factory=list)
    orphan_vectors: list[OrphanVector] = field(default_factory=list)

    total_materials: int = 0
    total_ref_videos: int = 0
    total_vector_points: int = 0

    def print(self) -> None:
        print()
        print("=" * 60)
        print("FlowCut 三方存储一致性体检报告")
        print("=" * 60)
        print(f"MySQL fc_material         : {self.total_materials}")
        print(f"MySQL fc_reference_video  : {self.total_ref_videos}")
        print(f"Qdrant points             : {self.total_vector_points}")
        print("-" * 60)
        print(f"素材问题   : {len(self.material_issues)}")
        for issue in self.material_issues:
            print(f"  [fc_material {issue.material_id} | {issue.tenant_key}] "
                  f"{issue.kind} — {issue.detail}")
        print(f"参考视频问题 : {len(self.ref_video_issues)}")
        for issue in self.ref_video_issues:
            print(f"  [fc_reference_video {issue.ref_video_id} | {issue.tenant_key}] "
                  f"{issue.kind} — {issue.detail}")
        print(f"Qdrant 孤儿 point : {len(self.orphan_vectors)}")
        for o in self.orphan_vectors[:50]:
            print(f"  point_id={o.point_id}")
        if len(self.orphan_vectors) > 50:
            print(f"  ... 还有 {len(self.orphan_vectors) - 50} 条略")
        print("=" * 60)


async def _list_all_materials(db: Database) -> list[dict]:
    async with db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, tenant_key, oss_key, status, description, "
                "transcript, product, scene_role, vector_indexed "
                "FROM fc_material"
            )
            rows = await cur.fetchall()
            cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


async def _list_all_ref_videos(db: Database) -> list[dict]:
    async with db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, tenant_key, oss_key, audio_oss_key, status "
                "FROM fc_reference_video"
            )
            rows = await cur.fetchall()
            cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


async def _mark_material_failed(db: Database, material_id: int, reason: str) -> None:
    async with db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE fc_material SET status='FAILED', "
                "description=CONCAT(IFNULL(description,''), %s) WHERE id=%s",
                (f"\n[audit] {reason}", material_id),
            )
            await conn.commit()


async def _mark_ref_video_failed(db: Database, ref_video_id: int, reason: str) -> None:
    async with db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE fc_reference_video SET status='FAILED' WHERE id=%s",
                (ref_video_id,),
            )
            await conn.commit()


async def _audit(
    db: Database,
    oss,
    vector_store: VectorStore,
) -> tuple[AuditReport, set[int]]:
    """返回 (report, mysql_material_ids)。"""
    report = AuditReport()

    materials = await _list_all_materials(db)
    ref_videos = await _list_all_ref_videos(db)
    point_ids = await vector_store.list_all_point_ids()

    report.total_materials = len(materials)
    report.total_ref_videos = len(ref_videos)
    report.total_vector_points = len(point_ids)

    mysql_ids = {m["id"] for m in materials}
    point_id_set = set(point_ids)

    # ── fc_material ─────────────────────────────────────────────
    for m in materials:
        mid = m["id"]
        tk = m["tenant_key"]

        if m["oss_key"]:
            try:
                exists = oss.object_exists(m["oss_key"])
            except Exception as exc:
                exists = False
                logger.warning("HEAD %s failed: %s", m["oss_key"], exc)
            if not exists:
                report.material_issues.append(MaterialIssue(
                    material_id=mid, tenant_key=tk,
                    kind="missing_oss",
                    detail=f"oss_key={m['oss_key']}",
                ))

        if m["status"] == "READY" and m["description"]:
            if mid not in point_id_set:
                report.material_issues.append(MaterialIssue(
                    material_id=mid, tenant_key=tk,
                    kind="missing_vector",
                    detail=f"vector_indexed={m['vector_indexed']}",
                ))
            elif not m["vector_indexed"]:
                report.material_issues.append(MaterialIssue(
                    material_id=mid, tenant_key=tk,
                    kind="stale_vector_flag",
                    detail="Qdrant has point but vector_indexed=0",
                ))

    # ── fc_reference_video ──────────────────────────────────────
    for v in ref_videos:
        vid = v["id"]
        tk = v["tenant_key"]
        if v["oss_key"]:
            try:
                exists = oss.object_exists(v["oss_key"])
            except Exception as exc:
                exists = False
                logger.warning("HEAD %s failed: %s", v["oss_key"], exc)
            if not exists:
                report.ref_video_issues.append(RefVideoIssue(
                    ref_video_id=vid, tenant_key=tk,
                    kind="missing_oss_main",
                    detail=f"oss_key={v['oss_key']}",
                ))
        if v["audio_oss_key"]:
            try:
                exists = oss.object_exists(v["audio_oss_key"])
            except Exception as exc:
                exists = False
                logger.warning("HEAD %s failed: %s", v["audio_oss_key"], exc)
            if not exists:
                report.ref_video_issues.append(RefVideoIssue(
                    ref_video_id=vid, tenant_key=tk,
                    kind="missing_oss_audio",
                    detail=f"audio_oss_key={v['audio_oss_key']}",
                ))

    # ── Qdrant orphan points ────────────────────────────────────
    for pid in point_ids:
        if pid not in mysql_ids:
            report.orphan_vectors.append(OrphanVector(point_id=pid))

    return report, mysql_ids


async def _fix(
    report: AuditReport,
    db: Database,
    material_repo: MaterialRepository,
    embedding: EmbeddingService,
    vector_store: VectorStore,
    *,
    fix_missing_vectors: bool,
    fix_missing_oss: bool,
    fix_orphan_vectors: bool,
) -> None:
    if fix_missing_oss:
        for issue in report.material_issues:
            if issue.kind == "missing_oss":
                logger.info("FIX missing_oss → fc_material %d FAILED", issue.material_id)
                await _mark_material_failed(db, issue.material_id, issue.detail)
        for issue in report.ref_video_issues:
            if issue.kind in ("missing_oss_main", "missing_oss_audio"):
                logger.info("FIX missing_oss → fc_reference_video %d FAILED",
                            issue.ref_video_id)
                await _mark_ref_video_failed(db, issue.ref_video_id, issue.detail)

    if fix_missing_vectors:
        for issue in report.material_issues:
            if issue.kind not in ("missing_vector", "stale_vector_flag"):
                continue
            mat = await material_repo.get(issue.material_id)
            if not mat or not mat.get("description"):
                continue
            try:
                desc_vec = await embedding.embed(mat["description"])
                transcript = mat.get("transcript")
                transcript_vec = (
                    await embedding.embed(transcript) if transcript else None
                )
                payload = {
                    "tenant_key": mat["tenant_key"],
                    "product": mat.get("product"),
                    "scene_role": mat.get("scene_role"),
                    "status": "READY",
                    "has_transcript": bool(transcript),
                }
                await vector_store.upsert(
                    mat["id"], desc_vec, transcript_vec, payload
                )
                await material_repo.mark_vector_indexed(mat["id"])
                logger.info("FIX missing_vector → fc_material %d upserted", mat["id"])
            except Exception as exc:
                logger.warning("FIX missing_vector fc_material %d failed: %s",
                               mat["id"], exc)

    if fix_orphan_vectors:
        for orphan in report.orphan_vectors:
            try:
                await vector_store.delete(orphan.point_id)
                logger.info("FIX orphan_vector → deleted point %d", orphan.point_id)
            except Exception as exc:
                logger.warning("delete orphan point %d failed: %s",
                               orphan.point_id, exc)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true",
                        help="启用全部三类自动修复")
    parser.add_argument("--fix-missing-vectors", action="store_true")
    parser.add_argument("--fix-missing-oss", action="store_true")
    parser.add_argument("--fix-orphan-vectors", action="store_true")
    args = parser.parse_args(argv)

    fix_v = args.fix or args.fix_missing_vectors
    fix_o = args.fix or args.fix_missing_oss
    fix_orphan = args.fix or args.fix_orphan_vectors

    env = os.environ.get("FLOWCUT_ENV", "dev")
    if (fix_v or fix_o or fix_orphan) and env not in ("dev", "test"):
        print(f"拒绝在 FLOWCUT_ENV={env} 下执行 --fix*；仅 dev/test 允许。",
              file=sys.stderr)
        return 1

    db = Database(**make_db_kwargs())
    await db.connect()
    material_repo = MaterialRepository(db)
    _ref_repo = ReferenceVideoRepository(db)  # 仅供日后扩展

    oss = build_oss_client()
    embedding_cfg = make_embedding_config()
    embedding = build_embedding_service(embedding_cfg)
    vector_size = int(embedding_cfg["vector_size"] or 0)
    if vector_size <= 0:
        probe_vec = await embedding.embed("Flowcut embedding dimension probe")
        vector_size = len(probe_vec)
        if vector_size <= 0:
            raise RuntimeError("Embedding provider returned an empty probe vector")
    vector_store = VectorStore(
        make_qdrant_url(),
        vector_size=vector_size,
    )

    try:
        report, _ = await _audit(db, oss, vector_store)
        report.print()

        if fix_v or fix_o or fix_orphan:
            print()
            print(f"开始修复: missing_vectors={fix_v} missing_oss={fix_o} "
                  f"orphan_vectors={fix_orphan}")
            await _fix(
                report, db, material_repo, embedding, vector_store,
                fix_missing_vectors=fix_v,
                fix_missing_oss=fix_o,
                fix_orphan_vectors=fix_orphan,
            )
            print("修复执行完毕。建议再次执行一次（不带 --fix）核对剩余问题。")
        else:
            if (report.material_issues or report.ref_video_issues
                    or report.orphan_vectors):
                print("\n如需自动修复，重跑加 --fix （仅 dev/test 环境允许）。")

        return 0
    finally:
        await db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
