"""AppContainer — 应用依赖的统一组装与生命周期管理。

build_container() 负责：
  - 初始化所有 repos / LLM / agents / stores
  - 启动后台 TaskWorker
  - 返回完整的 AppContainer 实例

server.py startup() 负责：
  - 调用 build_container()
  - 挂载 admin 路由（需要 app 实例，故留在 server.py）
  - 将 container 存入 app.state.container
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from loguru import logger

from simpleclaw.llm.volcengine import VolcengineLLM
from simpleclaw.runtime.scope_lock import ScopeLockRegistry
from simpleclaw.runtime.services import RuntimeServices
from simpleclaw.tools.registry import ToolRegistry

from Mojing.agent.cold_path import ColdPathHook
from Mojing.agent.first_token import FirstTokenAgent
from Mojing.agent.main_agent import MainAgent
from Mojing.agent.postprocess import PostprocessHook
from Mojing.api.event_hub import EventHub
from Mojing.api.session_ingress import MainSessionIngressCoordinator, UserTurnExecutionContext
from Mojing.config import (
    make_cabinet_product_research_timeout_min,
    make_db_kwargs,
    make_cabinet_import_url,
    make_deep_research_timeout_min,
    make_deep_research_url,
    make_dream_idle_threshold_s,
    make_device_command_timeout_s,
    make_device_command_url,
    make_device_dismiss_timeout_s,
    make_device_dismiss_url,
    make_device_status_timeout_s,
    make_device_status_url,
    make_baidu_map_ak,
    make_baidu_weather_url,
    make_first_token_enabled,
    make_first_token_llm_config,
    make_first_token_timeout_s,
    make_hook_llm_config,
    make_image_analysis_url,
    make_llm_config,
    make_photo_capture_wait_timeout_s,
    make_skin_diary_crop_timeout_s,
    make_skin_diary_crop_url,
    make_task_consumer_group,
    make_task_queue,
    make_task_stream_prefix,
    make_weather_timeout_s,
)
from Mojing.runtime.activations import RuntimeActivationService, build_runtime_task_failure_activation
from Mojing.runtime.dream_admission import MojingDreamAdmissionContextBuilder
from Mojing.runtime.photo_capture import PhotoCaptureCoordinator
from Mojing.runtime.streams import MojingTaskStream
from Mojing.runtime.task_types import MojingTaskType
from Mojing.storage.cron_repo import CronRepository
from Mojing.storage.completion_event_repo import CompletionEventRepository
from Mojing.storage.database import Database
from Mojing.storage.deep_report_repo import DeepReportRepository
from Mojing.storage.document_repo import DocumentRepository
from Mojing.storage.action_usage_repo import ActionUsageRepository
from Mojing.storage.dream_repo import DreamRepository
from Mojing.storage.image_repo import ImageRepository
from Mojing.storage.llm_cache_repo import LLMCacheRepository
from Mojing.storage.memory_ledger_repo import MemoryLedgerRepository
from Mojing.storage.memory_repo import MySQLMemory
from Mojing.storage.obligation_repo import ObligationRepository
from Mojing.storage.runtime_task_repo import RuntimeTaskRepository
from Mojing.storage.skincare_cabinet_repo import SkincareCabinetRepository
from Mojing.storage.session_repo import SessionRepository
from Mojing.storage.session_store import SessionStore
from Mojing.storage.skin_diary_result_repo import SkinDiaryResultRepository
from Mojing.storage.skin_profile_repo import SkinProfileRepository
from Mojing.storage.subagent_runtime_repo import SubagentRuntimeRepository
from Mojing.storage.subagent_store import SubagentStore
from Mojing.storage.tenant_state_repo import TenantStateRepository
from Mojing.storage.tool_invocation_repo import ToolInvocationRepository
from Mojing.tools.retrieve_evidence import RetrieveEvidenceTool


@dataclass
class AppContainer:
    # 核心基础设施
    db: Database
    llm: VolcengineLLM
    runtime: RuntimeServices
    event_hub: EventHub
    task_scope_locks: ScopeLockRegistry
    photo_capture_coordinator: PhotoCaptureCoordinator

    # Agent
    main_agent: MainAgent
    main_session_ingress: MainSessionIngressCoordinator | None
    first_token_agent: FirstTokenAgent | None
    sessions: SessionStore
    subagent_store: SubagentStore
    cron_scheduler: object  # CronScheduler

    # 子 Agent 实例（admin 路由需要）
    skin_diary_subagent: object
    deep_report_subagent: object

    # Repos（admin 路由 + 测试需要）
    doc_repo: DocumentRepository
    obligation_repo: ObligationRepository
    session_repo: SessionRepository
    runtime_task_repo: RuntimeTaskRepository
    image_repo: ImageRepository
    skincare_cabinet_repo: SkincareCabinetRepository
    skin_diary_result_repo: SkinDiaryResultRepository
    deep_report_repo: DeepReportRepository
    skin_profile_repo: SkinProfileRepository
    tenant_state_repo: TenantStateRepository
    llm_cache_repo: LLMCacheRepository
    tool_invocation_repo: ToolInvocationRepository
    action_usage_repo: ActionUsageRepository
    completion_event_repo: CompletionEventRepository
    subagent_runtime_repo: SubagentRuntimeRepository
    dream_repo: DreamRepository
    dream_scheduler: object  # DreamScheduler
    memory_ledger_repo: MemoryLedgerRepository

    # 后台 Worker tasks
    worker_tasks: list[asyncio.Task] = field(default_factory=list)


async def build_container() -> AppContainer:
    """初始化所有依赖，启动后台 Worker，返回 AppContainer。"""
    from Mojing.storage.database import ensure_schema
    app_container_ref: dict[str, AppContainer] = {}

    db = Database(**make_db_kwargs())
    await db.connect()
    await ensure_schema(db)
    logger.info("MySQL 已连接，Schema 确认完毕")

    registry = ToolRegistry()

    repo                   = SessionRepository(db)
    llm_cache_repo         = LLMCacheRepository(db)
    llm                    = VolcengineLLM(
        make_llm_config(),
        cache_repo=llm_cache_repo,
        prefix_cache_lane="agent",
    )
    hook_llm               = VolcengineLLM(
        make_hook_llm_config(),
        cache_repo=llm_cache_repo,
        prefix_cache_lane="hook",
    )
    doc_repo               = DocumentRepository(db)
    obligation_repo        = ObligationRepository(db)
    image_repo             = ImageRepository(db)
    skincare_cabinet_repo  = SkincareCabinetRepository(db)
    skin_diary_result_repo = SkinDiaryResultRepository(db)
    deep_report_repo       = DeepReportRepository(db)
    skin_profile_repo      = SkinProfileRepository(db)
    tenant_state_repo      = TenantStateRepository(db)
    tool_invocation_repo   = ToolInvocationRepository(db)
    action_usage_repo      = ActionUsageRepository(db)
    subagent_runtime_repo  = SubagentRuntimeRepository(db)

    from Mojing.agent.cron_scheduler import CronScheduler
    from Mojing.subagent.deep_report import DeepReportSubagent
    from Mojing.subagent.skin_diary import SkinDiarySubagent
    from Mojing.tools.cron_tools import (
        CronAddCronTool,
        CronAddIntervalTool,
        CronAddOnceTool,
        CronListTool,
        CronRemoveTool,
    )
    from Mojing.tools.deep_report_chat import DeepReportChatTool
    from Mojing.tools.device_command import DeviceCommandTool
    from Mojing.tools.device_dismiss import DeviceDismissTool
    from Mojing.tools.device_status import DeviceStatusTool
    from Mojing.tools.image_tools import AnalyzeImageTool
    from Mojing.tools.notify_skin_diary import NotifySkinDiaryChatTool
    from Mojing.tools.runtime_status import CheckRuntimeStatusTool
    from Mojing.tools.skincare_cabinet import (
        ConfirmSkincareCabinetRecordTool,
        ListSkincareCabinetProductsTool,
        LookupSkincareCabinetProductStatusTool,
        ResearchSkincareProductTool,
    )
    from Mojing.tools.weather import QueryWeatherTool
    from Mojing.services.weather import BaiduWeatherService
    from simpleclaw.tools.builtin.skill import LoadSkillTool, UnloadSkillTool

    _image_analysis_url       = make_image_analysis_url()
    _cabinet_import_url       = make_cabinet_import_url()
    _cabinet_product_timeout_min = make_cabinet_product_research_timeout_min()
    _deep_research_timeout_min = make_deep_research_timeout_min()
    _deep_research_url        = make_deep_research_url()
    _skin_diary_crop_url      = make_skin_diary_crop_url()
    _skin_diary_crop_timeout_s = make_skin_diary_crop_timeout_s()
    _device_command_url       = make_device_command_url()
    _device_command_timeout_s = make_device_command_timeout_s()
    _photo_capture_wait_timeout_s = make_photo_capture_wait_timeout_s()
    _device_status_url        = make_device_status_url()
    _device_status_timeout_s  = make_device_status_timeout_s()
    _device_dismiss_url       = make_device_dismiss_url()
    _device_dismiss_timeout_s = make_device_dismiss_timeout_s()
    _baidu_weather_url        = make_baidu_weather_url()
    _baidu_map_ak             = make_baidu_map_ak()
    _weather_timeout_s        = make_weather_timeout_s()
    weather_service = BaiduWeatherService(
        api_url=_baidu_weather_url,
        ak=_baidu_map_ak,
        timeout_s=_weather_timeout_s,
    )

    cron_repo        = CronRepository(db)
    task_repo        = RuntimeTaskRepository(db)
    memory_ledger_repo = MemoryLedgerRepository(db)
    completion_event_repo = CompletionEventRepository(db)
    dream_repo = DreamRepository(db)
    task_queue       = make_task_queue()
    task_consumer_group = make_task_consumer_group()
    task_stream_prefix = make_task_stream_prefix()
    runtime          = RuntimeServices(
        task_queue=task_queue,
        task_state_store=task_repo,
        action_usage_store=action_usage_repo,
    )
    task_scope_locks = ScopeLockRegistry()
    photo_capture_coordinator = PhotoCaptureCoordinator()
    registry.set_runtime_services(runtime)
    logger.info(
        "TaskQueue configured: type={} stream_prefix={} consumer_group={}",
        task_queue.__class__.__name__,
        task_stream_prefix,
        task_consumer_group,
    )

    skin_diary_subagent = SkinDiarySubagent(
        db=db,
        document_repo=doc_repo,
        llm=llm,
        hook_llm=hook_llm,
        obligation_repo=obligation_repo,
        session_repo=repo,
        llm_cache_repo=llm_cache_repo,
        image_repo=image_repo,
        crop_endpoint_url=_skin_diary_crop_url,
        crop_timeout_s=_skin_diary_crop_timeout_s,
        runtime_task_repo=task_repo,
        skincare_cabinet_repo=skincare_cabinet_repo,
        weather_service=weather_service,
    )
    deep_report_subagent = DeepReportSubagent(
        db=db,
        document_repo=doc_repo,
        llm=llm,
        hook_llm=hook_llm,
        obligation_repo=obligation_repo,
        session_repo=repo,
        llm_cache_repo=llm_cache_repo,
        endpoint_url=_deep_research_url,
        runtime_task_repo=task_repo,
        image_repo=image_repo,
        skin_profile_repo=skin_profile_repo,
    )
    subagents = [skin_diary_subagent, deep_report_subagent]

    _ALL_STAGES = frozenset({"novice", "explore", "mature"})
    _NOVICE_ONLY = frozenset({"novice"})
    _EXPLORE_AND_ABOVE = frozenset({"explore", "mature"})

    def _runtime_status_tool(*, include_deep_report: bool, include_skin_diary: bool) -> CheckRuntimeStatusTool:
        return CheckRuntimeStatusTool(
            runtime_task_repo=task_repo,
            image_repo=image_repo,
            include_deep_report=include_deep_report,
            include_skin_diary=include_skin_diary,
        )

    main_agent = MainAgent(
        db=db,
        document_repo=doc_repo,
        image_repo=image_repo,
        base_registry=registry,
        runtime_task_repo=task_repo,
        action_usage_repo=action_usage_repo,
        completion_event_repo=completion_event_repo,
        skincare_cabinet_repo=skincare_cabinet_repo,
        deep_report_repo=deep_report_repo,
        skin_profile_repo=skin_profile_repo,
        tenant_state_repo=tenant_state_repo,
        tool_invocation_store=tool_invocation_repo,
        tool_factories=[
            lambda tenant_key: RetrieveEvidenceTool(
                llm=llm,
                memory=MySQLMemory(db, tenant_key=tenant_key, source="main"),
                image_repo=image_repo,
            ),
            lambda _: AnalyzeImageTool(
                image_repo=image_repo,
                runtime_task_repo=task_repo,
            ),
            lambda _: LoadSkillTool(),
            lambda _: UnloadSkillTool(),
            lambda _: ResearchSkincareProductTool(
                image_repo=image_repo,
            ),
            lambda _: ListSkincareCabinetProductsTool(
                cabinet_repo=skincare_cabinet_repo,
            ),
            lambda _: LookupSkincareCabinetProductStatusTool(
                cabinet_repo=skincare_cabinet_repo,
                runtime_task_repo=task_repo,
            ),
            lambda _: ConfirmSkincareCabinetRecordTool(
                cabinet_repo=skincare_cabinet_repo,
            ),
            lambda _: QueryWeatherTool(
                weather_service=weather_service,
            ),
            lambda _: CronAddOnceTool(cron_repo),
            lambda _: CronAddIntervalTool(cron_repo),
            lambda _: CronAddCronTool(cron_repo),
            lambda _: CronListTool(cron_repo),
            lambda _: CronRemoveTool(cron_repo),
        ],
        device_tool_factories=[
            lambda _: DeviceCommandTool(
                api_url=_device_command_url,
                timeout_s=_device_command_timeout_s,
                photo_capture_coordinator=photo_capture_coordinator,
                photo_wait_timeout_s=_photo_capture_wait_timeout_s,
            ),
            lambda _: DeviceStatusTool(
                api_url=_device_status_url,
                timeout_s=_device_status_timeout_s,
            ),
            lambda _: DeviceDismissTool(
                api_url=_device_dismiss_url,
                timeout_s=_device_dismiss_timeout_s,
            ),
        ],
        staged_tool_factories=[
            (_NOVICE_ONLY, lambda _: _runtime_status_tool(include_deep_report=False, include_skin_diary=True)),
            (_EXPLORE_AND_ABOVE, lambda _: _runtime_status_tool(include_deep_report=True, include_skin_diary=True)),
            (_ALL_STAGES, lambda _: NotifySkinDiaryChatTool(
                runtime_task_repo=task_repo,
                image_repo=image_repo,
            )),
            (_EXPLORE_AND_ABOVE, lambda _: DeepReportChatTool(
                image_repo=image_repo,
                runtime_task_repo=task_repo,
            )),
        ],
    )
    logger.info("MainAgent 就绪")

    first_token_agent = (
        FirstTokenAgent(
            llm=VolcengineLLM(
                make_first_token_llm_config(),
                cache_repo=llm_cache_repo,
                prefix_cache_lane="first_token",
            ),
            cache_repo=llm_cache_repo,
            timeout_s=make_first_token_timeout_s(),
            enabled=True,
        )
        if make_first_token_enabled()
        else None
    )
    logger.info("FirstTokenAgent {}", "enabled" if first_token_agent else "disabled")

    from simpleclaw.context.compressor import ContextCompressor
    from Mojing.agent.memory_extract import (
        make_memory_extract_executor,
        make_memory_extract_submitter,
    )

    main_compressor        = ContextCompressor(max_tokens=2200, target_tokens=1000, min_keep_tokens=600)
    main_memory_extractor  = make_memory_extract_submitter(
        runtime=runtime,
        source="main",
        memory_ledger_store=memory_ledger_repo,
    )
    subagent_compressor    = ContextCompressor(max_tokens=2200, target_tokens=1000, min_keep_tokens=600)
    subagent_memory_extractors = {
        sub.memory_source(): make_memory_extract_submitter(
            runtime=runtime,
            source=sub.memory_source(),
            memory_ledger_store=memory_ledger_repo,
        )
        for sub in subagents
    }

    event_hub = EventHub()

    sessions = SessionStore(
        llm=llm,
        main_agent=main_agent,
        session_repo=repo,
        tenant_state_repo=tenant_state_repo,
        compressor=main_compressor,
        memory_extractor=main_memory_extractor,
        memory_ledger_store=memory_ledger_repo,
    )
    logger.info("SessionStore 就绪")

    subagent_store = SubagentStore(
        llm=llm,
        subagents=subagents,
        session_repo=repo,
        session_store=sessions,
        postprocess_runtime=runtime,
        compressor=subagent_compressor,
        memory_extractors=subagent_memory_extractors,
        memory_ledger_store=memory_ledger_repo,
        subagent_runtime_repo=subagent_runtime_repo,
        publish_fn=event_hub.publish,
    )
    logger.info("SubagentStore 就绪：subagents={}", [a.name for a in subagents])

    app_container: AppContainer | None = None

    async def _enqueue_skin_diary_activation(**kwargs):
        if app_container is None or app_container.main_session_ingress is None:
            raise RuntimeError("main_session_ingress is not ready")
        return await app_container.main_session_ingress.submit_system_activation(**kwargs)

    activation_service = RuntimeActivationService(
        enqueue_fn=_enqueue_skin_diary_activation,
    )

    async def _enqueue_runtime_task_failure_activation(task, error: str) -> None:
        request = build_runtime_task_failure_activation(
            tenant_key=str(task.tenant_key or task.payload.get("tenant_key") or "").strip(),
            source_session_key=str(task.session_key or task.payload.get("session_key") or ""),
            task_id=str(task.task_id or ""),
            task_type=str(task.task_type or ""),
            error=error,
            business_ref_type=str(task.payload.get("business_ref_type") or "") or None,
            business_ref_id=str(task.payload.get("business_ref_id") or task.payload.get("job_id") or "") or None,
        )
        if request is None:
            return
        try:
            await activation_service.enqueue(request)
        except Exception as exc:
            logger.warning(
                "runtime task failure activation enqueue failed: type={} task_id={} err={}",
                task.task_type,
                task.task_id,
                exc,
            )

    cron_scheduler = CronScheduler(
        cron_repo=cron_repo,
        session_store=sessions,
        publish_fn=event_hub.publish,
        activation_service=activation_service,
    )
    cron_scheduler.start()

    postprocess_hook = PostprocessHook(llm=hook_llm, document_repo=doc_repo)

    cold_path_hook = ColdPathHook(
        llm=hook_llm,
        obligation_repo=obligation_repo,
        cache_repo=llm_cache_repo,
        runtime_task_repo=task_repo,
    )
    logger.info("PostrunHooks 初始化完成（postprocess / obligation_extract 已转为队列驱动）")

    # ------------------------------------------------------------------
    # 启动后台 Worker
    # ------------------------------------------------------------------
    from Mojing.runtime.executors import (
        make_cabinet_product_research_executor,
        make_cabinet_product_record_executor,
        make_deep_research_executor,
        make_image_analysis_executor,
        make_skin_diary_generation_executor,
        make_postprocess_executor,
        make_skin_profile_sync_executor,
        make_obligation_extract_executor,
        make_structured_memory_executor,
        make_subagent_dispatch_executor,
    )
    from simpleclaw.dream import DreamExecutor, DreamScheduler
    from Mojing.dream import MojingDreamSubagentRunner
    from Mojing.runtime.dream_monitor import MemoryLedgerDreamMonitor
    from Mojing.runtime.triggered_monitor import WaitExternalTaskMonitor
    from Mojing.runtime.worker import TaskWorker

    postprocess_executors = {
        "postprocess": make_postprocess_executor(postprocess_hook),
        "skin_profile_sync": make_skin_profile_sync_executor(
            skin_repo=skin_profile_repo,
            document_repo=doc_repo,
            image_repo=image_repo,
            tenant_state_repo=tenant_state_repo,
            action_usage_repo=action_usage_repo,
            skin_diary_result_repo=skin_diary_result_repo,
            runtime_task_repo=task_repo,
            runtime=runtime,
            activation_service=activation_service,
            obligation_repo=obligation_repo,
        ),
    }
    obligation_extract_executors = {
        "obligation_extract": make_obligation_extract_executor(
            cold_path_hook,
            runtime_task_repo=task_repo,
            obligation_repo=obligation_repo,
            runtime=runtime,
            skin_profile_repo=skin_profile_repo,
        ),
        # 兼容旧队列中尚未消费的 structured_memory 任务。
        "structured_memory": make_structured_memory_executor(
            cold_path_hook,
            runtime_task_repo=task_repo,
            obligation_repo=obligation_repo,
            runtime=runtime,
            skin_profile_repo=skin_profile_repo,
        ),
    }
    for sub in subagents:
        sub_postprocess_hook = sub.make_postprocess_hook()
        if sub_postprocess_hook is not None:
            postprocess_executors[f"{sub.name}_postprocess"] = make_postprocess_executor(sub_postprocess_hook)

    # 兼容旧 postprocess stream 中尚未消费的 structured_memory 任务
    legacy_postprocess_executors = {**postprocess_executors, **obligation_extract_executors}

    def _make_worker(stream, executors):
        return TaskWorker(
            task_queue,
            stream,
            consumer_group=task_consumer_group,
            executors=executors,
            task_state_store=task_repo,
            scope_locks=task_scope_locks,
            action_usage_store=action_usage_repo,
            final_failure_handler=_enqueue_runtime_task_failure_activation,
        )

    postprocess_worker = _make_worker(MojingTaskStream.POSTPROCESS, legacy_postprocess_executors)
    obligation_extract_worker = _make_worker(MojingTaskStream.OBLIGATION_EXTRACT, obligation_extract_executors)
    image_analysis_worker = _make_worker(
        MojingTaskStream.IMAGE_ANALYSIS,
        {
            "image_analysis": make_image_analysis_executor(
                endpoint_url=_image_analysis_url,
                image_repo=image_repo,
            ),
        },
    )
    skin_diary_worker = _make_worker(
        MojingTaskStream.SKIN_DIARY,
        {
            "skin_diary_generation": make_skin_diary_generation_executor(
                llm=llm,
                document_repo=doc_repo,
                skin_profile_repo=skin_profile_repo,
                skin_diary_result_repo=skin_diary_result_repo,
                skincare_cabinet_repo=skincare_cabinet_repo,
                runtime_task_repo=task_repo,
                publish_fn=event_hub.publish,
                activation_service=activation_service,
                tenant_state_repo=tenant_state_repo,
                sessions=sessions,
                weather_service=weather_service,
                crop_endpoint_url=_skin_diary_crop_url,
                crop_timeout_s=_skin_diary_crop_timeout_s,
            ),
        },
    )
    memory_extract_worker = _make_worker(
        MojingTaskStream.MEMORY_EXTRACT,
        {"memory_extract": make_memory_extract_executor(
            llm=hook_llm,
            db=db,
            memory_ledger_store=memory_ledger_repo,
            runtime_task_repo=task_repo,
            document_repo=doc_repo,
            skin_profile_repo=skin_profile_repo,
        )},
    )
    cabinet_product_worker = _make_worker(
        MojingTaskStream.CABINET_PRODUCT,
        {
            "cabinet_product_research": make_cabinet_product_research_executor(
                endpoint_url=_cabinet_import_url,
                cabinet_repo=skincare_cabinet_repo,
            ),
            "cabinet_product_record": make_cabinet_product_record_executor(
                cabinet_repo=skincare_cabinet_repo,
            ),
        },
    )
    deep_research_worker = _make_worker(
        MojingTaskStream.DEEP_RESEARCH,
        {"deep_research": make_deep_research_executor(endpoint_url=_deep_research_url)},
    )
    subagent_dispatch_worker = _make_worker(
        MojingTaskStream.SUBAGENT_DISPATCH,
        {"subagent_dispatch": make_subagent_dispatch_executor(subagent_store)},
    )
    background_worker = _make_worker(
        MojingTaskStream.BACKGROUND,
        {
            "deep_research":    make_deep_research_executor(endpoint_url=_deep_research_url),
            "subagent_dispatch": make_subagent_dispatch_executor(subagent_store),
            "dream": DreamExecutor(
                store=dream_repo,
                runner=MojingDreamSubagentRunner(
                    db=db,
                    memory_ledger_repo=memory_ledger_repo,
                    session_repo=repo,
                    document_repo=doc_repo,
                    runtime_task_repo=task_repo,
                    llm=hook_llm,
                    skin_profile_repo=skin_profile_repo,
                ),
            ),
        },
    )
    dream_scheduler = DreamScheduler(
        store=dream_repo,
        runtime=runtime,
        stream=MojingTaskStream.BACKGROUND,
        task_type=MojingTaskType.DREAM,
    )
    dream_admission = MojingDreamAdmissionContextBuilder(
        ingress_getter=lambda: (
            app_container_ref["container"].main_session_ingress
            if "container" in app_container_ref
            else None
        ),
    )
    dream_monitor = MemoryLedgerDreamMonitor(
        memory_ledger_repo=memory_ledger_repo,
        scheduler=dream_scheduler,
        session_repo=repo,
        idle_threshold_s=make_dream_idle_threshold_s(),
        admission_context_factory=dream_admission.build,
    )

    wait_external_task_monitor = WaitExternalTaskMonitor(
        runtime_task_repo=task_repo,
        deep_report_repo=deep_report_repo,
        skin_profile_repo=skin_profile_repo,
        skincare_cabinet_repo=skincare_cabinet_repo,
        image_repo=image_repo,
        document_repo=doc_repo,
        runtime=runtime,
        activation_service=activation_service,
        obligation_repo=obligation_repo,
        cabinet_product_timeout_min=_cabinet_product_timeout_min,
        deep_research_timeout_min=_deep_research_timeout_min,
        claimed_by_values=(
            image_analysis_worker.consumer_name,
            cabinet_product_worker.consumer_name,
            deep_research_worker.consumer_name,
        ),
    )

    worker_tasks = [
        asyncio.create_task(postprocess_worker.run(), name="worker-postprocess"),
        asyncio.create_task(obligation_extract_worker.run(), name="worker-obligation-extract"),
        asyncio.create_task(image_analysis_worker.run(), name="worker-image-analysis"),
        asyncio.create_task(skin_diary_worker.run(), name="worker-skin-diary"),
        asyncio.create_task(memory_extract_worker.run(), name="worker-memory-extract"),
        asyncio.create_task(cabinet_product_worker.run(), name="worker-cabinet-product"),
        asyncio.create_task(deep_research_worker.run(), name="worker-deep-research"),
        asyncio.create_task(subagent_dispatch_worker.run(), name="worker-subagent-dispatch"),
        asyncio.create_task(background_worker.run(), name="worker-background-legacy"),
        asyncio.create_task(dream_monitor.run(), name="worker-dream-monitor"),
        asyncio.create_task(wait_external_task_monitor.run(), name="worker-wait-external-monitor"),
    ]
    logger.info(
        "TaskWorkers 已启动：postprocess / obligation_extract / image_analysis / memory_extract / "
        "skin_diary / cabinet_product / deep_research / subagent_dispatch / dream-monitor / "
        "wait_external-monitor / background(legacy)"
    )

    app_container = AppContainer(
        db=db,
        llm=llm,
        runtime=runtime,
        event_hub=event_hub,
        task_scope_locks=task_scope_locks,
        photo_capture_coordinator=photo_capture_coordinator,
        main_agent=main_agent,
        main_session_ingress=None,
        first_token_agent=first_token_agent,
        sessions=sessions,
        subagent_store=subagent_store,
        cron_scheduler=cron_scheduler,
        skin_diary_subagent=skin_diary_subagent,
        deep_report_subagent=deep_report_subagent,
        doc_repo=doc_repo,
        obligation_repo=obligation_repo,
        session_repo=repo,
        runtime_task_repo=task_repo,
        image_repo=image_repo,
        skincare_cabinet_repo=skincare_cabinet_repo,
        skin_diary_result_repo=skin_diary_result_repo,
        deep_report_repo=deep_report_repo,
        skin_profile_repo=skin_profile_repo,
        tenant_state_repo=tenant_state_repo,
        llm_cache_repo=llm_cache_repo,
        tool_invocation_repo=tool_invocation_repo,
        action_usage_repo=action_usage_repo,
        completion_event_repo=completion_event_repo,
        subagent_runtime_repo=subagent_runtime_repo,
        dream_repo=dream_repo,
        dream_scheduler=dream_scheduler,
        memory_ledger_repo=memory_ledger_repo,
        worker_tasks=worker_tasks,
    )

    from Mojing.api.routes.chat import _run_turn

    async def _run_ctx(ctx: UserTurnExecutionContext) -> None:
        await _run_turn(
            app_container,
            ctx.session_key,
            ctx.tenant_key,
            ctx.message,
            ctx.queue,
            on_text=ctx.on_text,
            on_done=ctx.on_done,
            on_error=ctx.on_error,
            on_first_token_text=ctx.on_first_token_text,
            on_first_token_status=ctx.on_first_token_status,
            media=ctx.media,
            message_id=ctx.message_id,
            device_id=ctx.device_id,
            device_code=ctx.device_code,
            prompt_surface=ctx.prompt_surface,
            capture_photo_enabled=ctx.capture_photo_enabled,
            report_id=ctx.report_id,
            origin_session_key=ctx.origin_session_key,
            ingress_id=ctx.ingress_id,
            on_prompt_messages=ctx.on_prompt_messages,
            on_attention_packets=ctx.on_attention_packets,
        )

    app_container.main_session_ingress = MainSessionIngressCoordinator(
        _run_ctx,
        sessions=sessions,
        publish_event=event_hub.publish,
        completion_event_repo=completion_event_repo,
    )
    app_container_ref["container"] = app_container
    return app_container
