"""VectorStore — Qdrant 向量搜索封装。

Collection: fc_material_vectors
Named vectors: desc_vec / transcript_vec，维度由 Flowcut embedding 配置决定。
"""
from __future__ import annotations

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels
from qdrant_client.http.exceptions import UnexpectedResponse


_COLLECTION_NAME = "fc_material_vectors"
_DISTANCE = qmodels.Distance.COSINE


class VectorStore:
    """对 Qdrant 的轻量异步封装，管理 fc_material_vectors 集合的增删查。"""

    def __init__(
        self,
        url: str,
        *,
        vector_size: int = 1024,
        collection_name: str = _COLLECTION_NAME,
    ) -> None:
        self._client = AsyncQdrantClient(url=url)
        self._vector_size = int(vector_size)
        self._collection_name = collection_name

    # ── 集合管理 ────────────────────────────────────────────

    async def ensure_collection(self) -> None:
        """启动时调用，collection 不存在则创建。"""
        try:
            collections = await self._client.get_collections()
            names = {c.name for c in collections.collections}
        except UnexpectedResponse:
            names = set()

        if self._collection_name not in names:
            await self._client.create_collection(
                collection_name=self._collection_name,
                vectors_config={
                    "desc_vec": qmodels.VectorParams(
                        size=self._vector_size,
                        distance=_DISTANCE,
                    ),
                    "transcript_vec": qmodels.VectorParams(
                        size=self._vector_size,
                        distance=_DISTANCE,
                    ),
                },
            )

    # ── 写入 ────────────────────────────────────────────────

    async def upsert(
        self,
        material_id: int,
        desc_vector: list[float],
        transcript_vector: list[float] | None,
        payload: dict,
    ) -> None:
        """插入/更新一个 point。transcript_vector 为 None 时不写入该 named vector。"""
        vectors: dict[str, list[float]] = {"desc_vec": desc_vector}
        if transcript_vector is not None:
            vectors["transcript_vec"] = transcript_vector

        await self._client.upsert(
            collection_name=self._collection_name,
            points=[
                qmodels.PointStruct(
                    id=material_id,
                    vector=vectors,
                    payload=payload,
                )
            ],
        )

    # ── 搜索 ────────────────────────────────────────────────

    async def search(
        self,
        desc_query_vector: list[float] | None,
        transcript_query_vector: list[float] | None,
        tenant_key: str,
        product: str | None = None,
        scene_role: str | None = None,
        limit: int = 3,
    ) -> list[tuple[int, float]]:
        """双向量搜索，按 max(desc_score, transcript_score) 融合返回。

        Args:
            desc_query_vector: embed(seg.visual) 用于查 desc_vec；None 时跳过该路 query。
            transcript_query_vector: embed(seg.copy) 用于查 transcript_vec；None 时跳过该路 query。
            tenant_key: 租户隔离。
            product: 指定产品名；None 表示通用素材（product=NULL）。
            scene_role: MVP 阶段不启用，预留。
            limit: 返回条数。

        Returns:
            [(material_id, fused_score), ...]，按 fused_score 降序。两路 query 均为 None 时返回空列表。
        """
        if desc_query_vector is None and transcript_query_vector is None:
            return []
        must_filters: list[qmodels.Filter] = [
            qmodels.FieldCondition(key="tenant_key", match=qmodels.MatchValue(value=tenant_key)),
        ]

        if product is not None:
            must_filters.append(
                qmodels.FieldCondition(key="product", match=qmodels.MatchValue(value=product)),
            )
        else:
            must_filters.append(
                qmodels.IsNullCondition(is_null=qmodels.PayloadField(key="product")),
            )

        if scene_role is not None:
            must_filters.append(
                qmodels.FieldCondition(key="scene_role", match=qmodels.MatchValue(value=scene_role)),
            )

        query_filter = qmodels.Filter(must=must_filters)

        # 双向量并行搜索（qdrant-client 1.10+ 用 query_points，旧版 search 已移除）
        # 任一 query 为 None 时跳过该路 query，max-fusion 保留剩余路
        scores: dict[int, float] = {}

        if desc_query_vector is not None:
            desc_resp = await self._client.query_points(
                collection_name=self._collection_name,
                query=desc_query_vector,
                using="desc_vec",
                query_filter=query_filter,
                limit=limit,
                with_payload=False,
            )
            for hit in desc_resp.points:
                scores[hit.id] = max(scores.get(hit.id, 0.0), hit.score)

        if transcript_query_vector is not None:
            transcript_resp = await self._client.query_points(
                collection_name=self._collection_name,
                query=transcript_query_vector,
                using="transcript_vec",
                query_filter=query_filter,
                limit=limit,
                with_payload=False,
            )
            for hit in transcript_resp.points:
                scores[hit.id] = max(scores.get(hit.id, 0.0), hit.score)

        # 按 fused_score 降序
        result = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return result[:limit]

    # ── 删除 ────────────────────────────────────────────────

    async def delete(self, material_id: int) -> None:
        """删除指定 material_id 的 point。"""
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=qmodels.PointIdsList(points=[material_id]),
        )

    async def list_all_point_ids(self) -> list[int]:
        """通过 scroll 拉取 collection 内所有 point id，供审计脚本对账使用。"""
        ids: list[int] = []
        offset = None
        while True:
            points, next_offset = await self._client.scroll(
                collection_name=self._collection_name,
                limit=1000,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            ids.extend(int(p.id) for p in points)
            if next_offset is None:
                break
            offset = next_offset
        return ids
