"""AppContainer — FlowCut 应用依赖的统一组装与生命周期管理。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from loguru import logger
from simpleclaw.llm.gemini import GeminiLLM
from simpleclaw.llm.base import LLMProvider, ProviderConfig
from simpleclaw.llm.config import GeminiConfig, VolcengineConfig
from simpleclaw.llm.volcengine import VolcengineLLM
from simpleclaw.runtime.scope_lock import ScopeLockRegistry
from simpleclaw.runtime.services import RuntimeServices
from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.tools.registry import ToolRegistry

from Flowcut.agent.first_token import FirstTokenAgent
from Flowcut.agent.main_agent import MainAgent
from Flowcut.config import (
    make_db_kwargs,
    make_embedding_config,
    make_first_token_enabled,
    make_first_token_llm_config,
    make_first_token_timeout_s,
    make_llm_config,
    make_qc_cdp_url,
    make_qdrant_url,
    make_task_queue,
)
from Flowcut.runtime.reconcile import reconcile_orphan_tasks
from Flowcut.runtime.highlight_continuation import recover_active_highlight_batches
from Flowcut.runtime.streams import FlowcutTaskStream
from Flowcut.runtime.worker import make_workers
from Flowcut.storage.database import Database, ensure_schema
from Flowcut.storage.highlight_asset_repo import HighlightAssetRepository
from Flowcut.storage.highlight_batch_repo import HighlightBatchRepository
from Flowcut.storage.material_repo import MaterialRepository
from Flowcut.storage.oss_client import OSSClient, build_oss_client
from Flowcut.storage.creative_repo import CreativeRepository
from Flowcut.storage.script_repo import ScriptRepository
from Flowcut.storage.qianchuan_repo import QianchuanRepository
from Flowcut.storage.session_repo import SessionRepository
from Flowcut.storage.session_store import SessionStore
from Flowcut.storage.user_repo import LoginSessionRepository, UserRepository
from Flowcut.storage.task_repo import RuntimeTaskRepository
from Flowcut.storage.reference_video_repo import ReferenceVideoRepository
from Flowcut.storage.vector_store import VectorStore
from Flowcut.services.embedding import EmbeddingService, build_embedding_service


_VECTOR_REPAIR_INTERVAL_S = 600  # 10 分钟


@dataclass
class AppContainer:
    db: Database
    llm: LLMProvider
    runtime: RuntimeServices
    task_scope_locks: ScopeLockRegistry

    main_agent: MainAgent
    first_token_agent: FirstTokenAgent | None
    sessions: SessionStore

    # Repos（routes 需要）
    material_repo: MaterialRepository
    highlight_asset_repo: HighlightAssetRepository
    highlight_batch_repo: HighlightBatchRepository
    creative_repo: CreativeRepository
    script_repo: ScriptRepository
    qianchuan_repo: QianchuanRepository
    session_repo: SessionRepository
    task_repo: RuntimeTaskRepository
    ref_video_repo: ReferenceVideoRepository
    user_repo: UserRepository
    login_session_repo: LoginSessionRepository

    # Vector
    vector_store: VectorStore
    embedding_service: EmbeddingService

    # OSS
    oss_client: OSSClient

    worker_tasks: list[asyncio.Task] = field(default_factory=list)
    # zip 上传临时缓存 {upload_id: {"tenant_key": str, "zip_path": str, "preview": list, "created_at": float}}
    zip_uploads: dict[str, dict] = field(default_factory=dict)


async def build_container() -> AppContainer:
    """初始化所有依赖，启动后台 Worker + 修复周期任务，返回 AppContainer。"""
    db = Database(**make_db_kwargs())
    await db.connect()
    await ensure_schema(db)
    logger.info("MySQL 已连接，Schema 确认完毕")

    # Repos
    session_repo   = SessionRepository(db)
    task_repo      = RuntimeTaskRepository(db)
    material_repo  = MaterialRepository(db)
    highlight_asset_repo = HighlightAssetRepository(db)
    highlight_batch_repo = HighlightBatchRepository(db)
    creative_repo  = CreativeRepository(db)
    script_repo    = ScriptRepository(db)
    qianchuan_repo = QianchuanRepository(db)
    ref_video_repo = ReferenceVideoRepository(db)
    user_repo      = UserRepository(db)
    login_session_repo = LoginSessionRepository(db)
    logger.info("所有 Repository 初始化完毕")

    # Embedding + VectorStore
    embedding_cfg = make_embedding_config()
    embedding_service = build_embedding_service(embedding_cfg)
    vector_size = int(embedding_cfg["vector_size"] or 0)
    if vector_size <= 0:
        probe_vec = await embedding_service.embed("Flowcut embedding dimension probe")
        vector_size = len(probe_vec)
        if vector_size <= 0:
            raise RuntimeError("Embedding provider returned an empty probe vector")
        embedding_cfg["vector_size"] = vector_size
    vector_store = VectorStore(
        url=make_qdrant_url(),
        vector_size=vector_size,
    )
    try:
        await vector_store.ensure_collection()
        logger.info(
            "Qdrant Collection 确认完毕（embedding provider={} model={} dim={}）",
            embedding_cfg["provider"],
            embedding_cfg["model"],
            embedding_cfg["vector_size"],
        )
    except Exception:
        logger.warning(
            "Qdrant 不可达（url=%s），向量搜索功能暂不可用，服务继续启动",
            make_qdrant_url(),
        )

    # LLM
    llm = _build_llm(make_llm_config(), prefix_cache_lane="flowcut-main")

    # Runtime
    task_queue       = make_task_queue()
    runtime          = RuntimeServices(task_queue=task_queue, task_state_store=task_repo)
    task_scope_locks = ScopeLockRegistry()
    base_registry    = ToolRegistry()
    base_registry.set_runtime_services(runtime)

    # OSS
    oss_client = build_oss_client()

    # Tools
    from Flowcut.tools.decompose_video import DecomposeVideoTool
    from Flowcut.tools.create_cross_episode_highlights import CreateCrossEpisodeHighlightsTool
    from Flowcut.tools.generate_scripts import GenerateScriptsTool
    from Flowcut.tools.list_highlight_assets import ListHighlightAssetsTool
    from Flowcut.tools.search_materials import SearchMaterialsTool
    from Flowcut.tools.compose_video import ComposeVideoTool
    from Flowcut.tools.check_task_status import CheckTaskStatusTool
    from Flowcut.tools.publish_to_qianchuan import PublishToQianchuanTool
    from Flowcut.tools.upload_script import UploadScriptTool
    from Flowcut.tools.update_script import UpdateScriptTool
    from Flowcut.tools.match_by_script import MatchByScriptTool
    from Flowcut.tools.export_package import ExportPackageTool
    from Flowcut.tools.account_stats import GetAccountStatsTool
    from Flowcut.tools.search_creatives import SearchCreativesByNameTool
    from Flowcut.tools.creative_stats import GetCreativeStatsTool
    from Flowcut.tools.search_materials_by_name import SearchMaterialsByNameTool
    from Flowcut.tools.material_stats import GetMaterialStatsTool
    from Flowcut.tools.navigate_to import NavigateToTool

    tool_factories = [
        lambda _: DecomposeVideoTool(
            runtime=runtime,
            ref_video_repo=ref_video_repo,
            script_repo=script_repo,
        ),
        lambda _: ListHighlightAssetsTool(
            highlight_asset_repo=highlight_asset_repo,
        ),
        lambda _: CreateCrossEpisodeHighlightsTool(
            runtime=runtime,
            task_repo=task_repo,
            highlight_batch_repo=highlight_batch_repo,
            highlight_asset_repo=highlight_asset_repo,
        ),
        lambda _: GenerateScriptsTool(
            material_repo=material_repo,
            ref_video_repo=ref_video_repo,
        ),
        lambda _: SearchMaterialsTool(
            material_repo=material_repo,
            script_repo=script_repo,
            vector_store=vector_store,
            embedding_service=embedding_service,
        ),
        lambda _: ComposeVideoTool(runtime=runtime),
        lambda _: CheckTaskStatusTool(
            task_repo=task_repo,
            highlight_batch_repo=highlight_batch_repo,
        ),
        lambda _: PublishToQianchuanTool(runtime=runtime),
        lambda _: UploadScriptTool(script_repo=script_repo),
        lambda _: UpdateScriptTool(script_repo=script_repo),
        lambda _: MatchByScriptTool(
            script_repo=script_repo,
            embedding_service=embedding_service,
            vector_store=vector_store,
            material_repo=material_repo,
            oss_client=oss_client,
        ),
        lambda _: ExportPackageTool(runtime=runtime),
        lambda _: GetAccountStatsTool(qianchuan_repo=qianchuan_repo),
        lambda _: SearchCreativesByNameTool(creative_repo=creative_repo),
        lambda _: GetCreativeStatsTool(creative_repo=creative_repo),
        lambda _: SearchMaterialsByNameTool(material_repo=material_repo),
        lambda _: GetMaterialStatsTool(material_repo=material_repo),
        lambda _: NavigateToTool(),
    ]

    main_agent = MainAgent(
        db=db,
        task_repo=task_repo,
        material_repo=material_repo,
        script_repo=script_repo,
        creative_repo=creative_repo,
        base_registry=base_registry,
        tool_factories=tool_factories,
    )
    logger.info("MainAgent 就绪")

    first_token_agent = (
        FirstTokenAgent(
            llm=_build_llm(
                make_first_token_llm_config(),
                prefix_cache_lane="flowcut-first-token",
            ),
            cache_repo=None,  # type: ignore[arg-type]
            timeout_s=make_first_token_timeout_s(),
            enabled=True,
        )
        if make_first_token_enabled()
        else None
    )
    logger.info("FirstTokenAgent {}", "enabled" if first_token_agent else "disabled")

    sessions = SessionStore(
        llm=llm,
        main_agent=main_agent,
        session_repo=session_repo,
    )
    logger.info("SessionStore 就绪")

    # Clean abandoned highlight batches before generic orphan reconciliation.
    # Otherwise old child tasks are re-enqueued before their parent is expired.
    expired_batches = await highlight_batch_repo.fail_stale_active(
        max_age_hours=6,
    )
    closed_batch_tasks = await highlight_batch_repo.close_terminal_runtime_tasks()
    if expired_batches or closed_batch_tasks:
        logger.info(
            "highlight batch cleanup: expired {} batch(es), closed {} runtime task(s)",
            expired_batches,
            closed_batch_tasks,
        )

    # Workers（启动 7 条 Stream，含 VECTOR_REPAIR + QIANCHUAN_SYNC）
    import os as _os
    _qc_tenant_key = _os.getenv("FLOWCUT_DEFAULT_TENANT_KEY", "flowcut")
    workers = make_workers(
        task_queue, task_scope_locks, task_repo,
        material_repo=material_repo,
        highlight_asset_repo=highlight_asset_repo,
        ref_video_repo=ref_video_repo,
        script_repo=script_repo,
        embedding_service=embedding_service,
        vector_store=vector_store,
        oss_client=oss_client,
        creative_repo=creative_repo,
        qianchuan_repo=qianchuan_repo,
        qc_cdp_url=make_qc_cdp_url(),
        qc_tenant_key=_qc_tenant_key,
        runtime=runtime,
        highlight_batch_repo=highlight_batch_repo,
    )
    worker_tasks = [
        asyncio.create_task(w.run(), name=f"worker-{w.stream}")
        for w in workers
    ]
    logger.info("TaskWorkers 已启动：{} 条 stream", len(worker_tasks))

    # 启动 vector_repair 周期任务（每 10 分钟）
    async def _vector_repair_loop() -> None:
        while True:
            try:
                await asyncio.sleep(_VECTOR_REPAIR_INTERVAL_S)
                envelope = TaskEnvelope(
                    task_type="vector_repair",
                    payload={},
                    stream=FlowcutTaskStream.VECTOR_REPAIR,
                    tenant_key="",
                    scope_key="vector_repair:periodic",
                )
                await runtime.submit_task(envelope)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("vector_repair loop error: {}", exc)

    repair_task = asyncio.create_task(_vector_repair_loop(), name="vector-repair-loop")
    worker_tasks.append(repair_task)

    # 启动后扫描一次孤儿任务（卡死的 running + 内存队列丢失的 queued），重入队。
    try:
        recovered = await reconcile_orphan_tasks(db, runtime)
        if recovered:
            logger.info("orphan reconciler: re-enqueued {} task(s) on startup", recovered)
    except Exception as exc:
        logger.warning("orphan reconciler failed on startup: {}", exc)

    try:
        recovered_batches = await recover_active_highlight_batches(
            runtime=runtime,
            highlight_batch_repo=highlight_batch_repo,
        )
        if recovered_batches:
            logger.info(
                "highlight batch recovery: re-enqueued {} active batch(es)",
                recovered_batches,
            )
    except Exception as exc:
        logger.warning("highlight batch recovery failed on startup: {}", exc)

    return AppContainer(
        db=db,
        llm=llm,
        runtime=runtime,
        task_scope_locks=task_scope_locks,
        main_agent=main_agent,
        first_token_agent=first_token_agent,
        sessions=sessions,
        material_repo=material_repo,
        highlight_asset_repo=highlight_asset_repo,
        highlight_batch_repo=highlight_batch_repo,
        creative_repo=creative_repo,
        script_repo=script_repo,
        qianchuan_repo=qianchuan_repo,
        session_repo=session_repo,
        task_repo=task_repo,
        ref_video_repo=ref_video_repo,
        user_repo=user_repo,
        login_session_repo=login_session_repo,
        vector_store=vector_store,
        embedding_service=embedding_service,
        oss_client=oss_client,
        worker_tasks=worker_tasks,
    )


def _build_llm(
    config: ProviderConfig,
    *,
    prefix_cache_lane: str = "default",
) -> LLMProvider:
    if isinstance(config, GeminiConfig):
        return GeminiLLM(config)
    if isinstance(config, VolcengineConfig):
        return VolcengineLLM(config, prefix_cache_lane=prefix_cache_lane)
    raise TypeError(f"Unsupported LLM config type: {type(config)!r}")
