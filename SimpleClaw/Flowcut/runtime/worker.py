"""FlowCut TaskWorker 工厂。

make_workers() 根据 Stream 创建 TaskWorker 实例（不启动）。
调用方（server.py / container.py）负责 asyncio.create_task(worker.run())。
"""
from __future__ import annotations

import os

from simpleclaw.runtime.scope_lock import ScopeLockRegistry
from simpleclaw.runtime.task_queue import InMemoryTaskQueue, RedisTaskQueue
from simpleclaw.runtime.task_state import TaskStateStore
from simpleclaw.runtime.worker import TaskWorker

# MATERIAL_PROCESS worker 并发度。每个 worker 是一个独立的 consumer loop，
# 从内存队列里独立 pop。Gemini API 有 RPM 限制，默认 3 较稳妥。
_MATERIAL_PROCESS_CONCURRENCY = max(1, int(os.getenv("FLOWCUT_MATERIAL_PROCESS_CONCURRENCY", "3")))
_HIGHLIGHT_PLAN_CONCURRENCY = max(1, int(os.getenv("FLOWCUT_HIGHLIGHT_PLAN_CONCURRENCY", "1")))

# 新高光批量管道 worker 并发度
_HIGHLIGHT_BATCH_CONCURRENCY = max(1, int(os.getenv("FLOWCUT_HIGHLIGHT_BATCH_CONCURRENCY", "1")))
_HIGHLIGHT_EP_PREP_CONCURRENCY = max(1, int(os.getenv("FLOWCUT_HIGHLIGHT_EP_PREP_CONCURRENCY", "3")))
_HIGHLIGHT_MERGE_DEC_CONCURRENCY = max(1, int(os.getenv("FLOWCUT_HIGHLIGHT_MERGE_DEC_CONCURRENCY", "1")))
_HIGHLIGHT_START_SEL_CONCURRENCY = max(1, int(os.getenv("FLOWCUT_HIGHLIGHT_START_SEL_CONCURRENCY", "1")))
_HIGHLIGHT_SPAN_PLAN_CONCURRENCY = max(1, int(os.getenv("FLOWCUT_HIGHLIGHT_SPAN_PLAN_CONCURRENCY", "3")))

from Flowcut.runtime.executors import (
    make_export_package_executor,
    make_highlight_export_executor,
    make_highlight_plan_executor,
    make_material_process_executor,
    make_qianchuan_publish_executor,
    make_qianchuan_sync_executor,
    make_scene_decompose_executor,
    make_video_compose_executor,
    make_vector_repair_executor,
)
from Flowcut.runtime.highlight_episode_prepare import make_episode_prepare_executor
from Flowcut.runtime.highlight_merge_decompose import make_merge_decompose_executor
from Flowcut.runtime.highlight_start_select import make_start_select_executor
from Flowcut.runtime.highlight_span_plan import make_span_plan_executor
from Flowcut.runtime.highlight_batch import make_highlight_batch_executor
from Flowcut.storage.creative_repo import CreativeRepository
from Flowcut.storage.highlight_batch_repo import HighlightBatchRepository
from Flowcut.storage.oss_client import OSSClient
from Flowcut.storage.qianchuan_repo import QianchuanRepository
from Flowcut.runtime.streams import FlowcutTaskStream
from Flowcut.storage.material_repo import MaterialRepository
from Flowcut.storage.highlight_asset_repo import HighlightAssetRepository
from Flowcut.storage.reference_video_repo import ReferenceVideoRepository
from Flowcut.storage.script_repo import ScriptRepository
from Flowcut.storage.vector_store import VectorStore
from Flowcut.services.embedding import EmbeddingService


def make_workers(
    task_queue: InMemoryTaskQueue | RedisTaskQueue,
    task_scope_locks: ScopeLockRegistry,
    task_state_store: TaskStateStore,
    *,
    material_repo: MaterialRepository,
    highlight_asset_repo: HighlightAssetRepository,
    ref_video_repo: ReferenceVideoRepository,
    script_repo: ScriptRepository,
    embedding_service: EmbeddingService,
    vector_store: VectorStore,
    oss_client: OSSClient,
    creative_repo: CreativeRepository,
    qianchuan_repo: QianchuanRepository,
    qc_cdp_url: str,
    qc_tenant_key: str,
    runtime,
    highlight_batch_repo: HighlightBatchRepository | None = None,
) -> list[TaskWorker]:
    """创建 FlowCut TaskWorker 实例，不启动。

    Args:
        creative_repo: 成片仓库，用于 qianchuan_sync executor 的两段式对齐写库。
        qianchuan_repo: 千川仓库，用于 upsert_orphan。
        qc_cdp_url: CDP 调试端口 URL（从 config.make_qc_cdp_url() 读取）。
        qc_tenant_key: 当前 MVP 单账号的 tenant_key。
        highlight_batch_repo: 可选，高光批量管道仓库。为 None 时不启动新管道 workers。

    Returns:
        未启动的 TaskWorker 列表，调用方按需 asyncio.create_task(w.run())。
    """

    def _make_worker(stream: str, executors: dict) -> TaskWorker:
        return TaskWorker(
            task_queue,
            stream,
            consumer_group="flowcut",
            executors=executors,
            task_state_store=task_state_store,
            scope_locks=task_scope_locks,
        )

    # MATERIAL_PROCESS 多 worker 实例，共享同一个 executor 函数。
    # 共享 executor 函数能复用其内部捕获的依赖（material_repo / embedding / vector_store）；
    # InMemoryTaskQueue 是线程/协程安全的，多个消费者并发 pop 不会重复消费。
    material_process_executor = make_material_process_executor(
        material_repo, embedding_service, vector_store,
    )
    workers: list[TaskWorker] = []
    for _ in range(_MATERIAL_PROCESS_CONCURRENCY):
        workers.append(_make_worker(
            FlowcutTaskStream.MATERIAL_PROCESS,
            {"material_process": material_process_executor},
        ))

    workers.extend([
        _make_worker(
            FlowcutTaskStream.SCENE_DECOMPOSE,
            {
                "scene_decompose": make_scene_decompose_executor(
                    material_repo, ref_video_repo, embedding_service, vector_store,
                    script_repo, creative_repo=creative_repo,
                ),
            },
        ),
        _make_worker(
            FlowcutTaskStream.VIDEO_COMPOSE,
            {
                "highlight_compose": make_video_compose_executor(
                    creative_repo=creative_repo,
                    script_repo=script_repo,
                    ref_video_repo=ref_video_repo,
                    highlight_asset_repo=highlight_asset_repo,
                    oss_client=oss_client,
                ),
                "highlight_export": make_highlight_export_executor(
                    creative_repo=creative_repo,
                    highlight_asset_repo=highlight_asset_repo,
                    oss_client=oss_client,
                ),
            },
        ),
        _make_worker(
            FlowcutTaskStream.QIANCHUAN_PUBLISH,
            {
                "qianchuan_publish": make_qianchuan_publish_executor(
                    creative_repo,
                    oss_client,
                    cdp_url=qc_cdp_url,
                ),
            },
        ),
        _make_worker(
            FlowcutTaskStream.QIANCHUAN_SYNC,
            {
                "qianchuan_sync": make_qianchuan_sync_executor(
                    creative_repo,
                    qianchuan_repo,
                    cdp_url=qc_cdp_url,
                    tenant_key=qc_tenant_key,
                ),
            },
        ),
        _make_worker(
            FlowcutTaskStream.VECTOR_REPAIR,
            {
                "vector_repair": make_vector_repair_executor(
                    material_repo, embedding_service, vector_store,
                ),
            },
        ),
        _make_worker(
            FlowcutTaskStream.EXPORT_PACKAGE,
            {
                "export_package": make_export_package_executor(
                    script_repo=script_repo,
                    material_repo=material_repo,
                    ref_video_repo=ref_video_repo,
                    oss_client=oss_client,
                ),
            },
        ),
    ])

    highlight_plan_executor = make_highlight_plan_executor(
        runtime=runtime,
        highlight_asset_repo=highlight_asset_repo,
        creative_repo=creative_repo,
        oss_client=oss_client,
        task_state_store=task_state_store,
    )
    for _ in range(_HIGHLIGHT_PLAN_CONCURRENCY):
        workers.append(_make_worker(
            FlowcutTaskStream.HIGHLIGHT_PLAN,
            {"highlight_plan": highlight_plan_executor},
        ))

    # ── 新高光批量管道 workers ──
    if highlight_batch_repo is not None:
        # Batch orchestrator
        batch_exec = make_highlight_batch_executor(
            runtime=runtime,
            highlight_batch_repo=highlight_batch_repo,
            highlight_asset_repo=highlight_asset_repo,
            creative_repo=creative_repo,
        )
        for _ in range(_HIGHLIGHT_BATCH_CONCURRENCY):
            workers.append(_make_worker(
                FlowcutTaskStream.HIGHLIGHT_BATCH,
                {"highlight_batch": batch_exec},
            ))

        # Episode prepare (parallel per episode)
        ep_prep_exec = make_episode_prepare_executor(
            runtime=runtime,
            oss_client=oss_client,
            highlight_batch_repo=highlight_batch_repo,
        )
        for _ in range(_HIGHLIGHT_EP_PREP_CONCURRENCY):
            workers.append(_make_worker(
                FlowcutTaskStream.HIGHLIGHT_EPISODE_PREPARE,
                {"episode_prepare": ep_prep_exec},
            ))

        # Merge + decompose
        merge_dec_exec = make_merge_decompose_executor(
            runtime=runtime,
            oss_client=oss_client,
            highlight_batch_repo=highlight_batch_repo,
        )
        for _ in range(_HIGHLIGHT_MERGE_DEC_CONCURRENCY):
            workers.append(_make_worker(
                FlowcutTaskStream.HIGHLIGHT_MERGE_DECOMPOSE,
                {"merge_decompose": merge_dec_exec},
            ))

        # Start selection
        start_sel_exec = make_start_select_executor(
            runtime=runtime,
            highlight_batch_repo=highlight_batch_repo,
        )
        for _ in range(_HIGHLIGHT_START_SEL_CONCURRENCY):
            workers.append(_make_worker(
                FlowcutTaskStream.HIGHLIGHT_START_SELECT,
                {"start_select": start_sel_exec},
            ))

        # Span plan (parallel per candidate)
        span_plan_exec = make_span_plan_executor(
            runtime=runtime,
            oss_client=oss_client,
            highlight_batch_repo=highlight_batch_repo,
            highlight_asset_repo=highlight_asset_repo,
            creative_repo=creative_repo,
        )
        for _ in range(_HIGHLIGHT_SPAN_PLAN_CONCURRENCY):
            workers.append(_make_worker(
                FlowcutTaskStream.HIGHLIGHT_SPAN_PLAN,
                {"span_plan": span_plan_exec},
            ))

    return workers
