"""CronScheduler — 后台定时任务轮询执行器。

每 30 分钟查询一次 nb_cron_jobs，对所有到期任务发起一次 system activation。
配置 activation_service 时，cron 只负责把到期事实转换为统一激活协议；
未配置时保留旧的直接 ReactLoop.run() fallback。

设计约束：
  - 同一 session_key 同一时刻只允许一个任务在运行（_running_sessions 锁）
  - 每个 job 独立 create_task，互不阻塞
  - 失败时只记录日志，不停止调度器主循环
  - once 类型执行后 mark_done；interval/cron 类型执行后 update_next_run
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Awaitable, Callable

from loguru import logger
from Mojing.utils.cron_time import next_cron_run, now_local

if TYPE_CHECKING:
    from Mojing.runtime.activations import RuntimeActivationService
    from Mojing.storage.cron_repo import CronRepository
    from Mojing.storage.session_store import SessionStore

CronPublishFn = Callable[[str, str, dict], Awaitable[int]]


class CronScheduler:
    """后台轮询调度器，随 FastAPI lifespan 启动/停止。"""

    POLL_INTERVAL = 30 * 60  # 秒
    CLAIM_LIMIT = 50
    STALE_RUNNING_TTL_S = 900

    def __init__(
        self,
        cron_repo: CronRepository,
        session_store: SessionStore,
        publish_fn: CronPublishFn | None = None,
        activation_service: "RuntimeActivationService | None" = None,
    ) -> None:
        self._repo = cron_repo
        self._sessions = session_store
        self._publish_fn = publish_fn
        self._activation_service = activation_service
        self._task: asyncio.Task | None = None
        # 正在运行的 session_key 集合，防止同一会话并发触发
        self._running_sessions: set[str] = set()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        """在 event loop 中启动后台轮询任务。"""
        self._task = asyncio.create_task(self._loop(), name="cron_scheduler")
        logger.info("CronScheduler 已启动（poll_interval={}s）", self.POLL_INTERVAL)

    async def stop(self) -> None:
        """优雅停止调度器。"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("CronScheduler 已停止")

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception as exc:
                logger.warning("CronScheduler tick 异常: {}", exc)
            await asyncio.sleep(self.POLL_INTERVAL)

    async def _tick(self) -> None:
        """单次轮询：回收僵尸 running job，并认领到期 job。"""
        released = await self._repo.release_stale_running(max_age_s=self.STALE_RUNNING_TTL_S)
        if released:
            logger.warning("CronScheduler: 回收 {} 个 stale running job", released)

        jobs = await self._repo.claim_due_jobs(limit=self.CLAIM_LIMIT)
        if not jobs:
            return

        logger.debug("CronScheduler: 发现 {} 个到期任务", len(jobs))
        scheduled_sessions: set[str] = set()
        for job in jobs:
            job_id = job["id"]
            session_key = job["session_key"]
            if session_key in self._running_sessions or session_key in scheduled_sessions:
                logger.debug("CronScheduler: session {} 正在运行，释放 job={}", session_key, job_id)
                await self._repo.release_claim(job_id)
                continue
            self._running_sessions.add(session_key)
            scheduled_sessions.add(session_key)
            asyncio.create_task(
                self._run_job(job),
                name=f"cron_job_{job_id}",
            )

    # ------------------------------------------------------------------
    # 单个 job 执行
    # ------------------------------------------------------------------

    async def _run_job(self, job: dict) -> None:
        job_id     = job["id"]
        tenant_key = job["tenant_key"]
        session_key = job["session_key"]
        cron_type  = job["cron_type"]
        task       = job["task"]

        try:
            logger.info(
                "CronScheduler: 执行 job={} type={} tenant={} session={}",
                job_id, cron_type, tenant_key, session_key,
            )
            if self._activation_service is not None:
                await self._enqueue_activation(job)
            else:
                await self._execute_turn(tenant_key, session_key, task)
        except Exception as exc:
            logger.error("CronScheduler: job={} 执行失败: {}", job_id, exc)
            await self._repo.release_claim(job_id)
            return

        try:
            # 执行后更新状态；若这里失败，宁可暂留 running 交给 stale recovery，
            # 也不要立刻 release 回 active 导致 once 任务重复触发。
            if cron_type == "once":
                await self._repo.mark_done(job_id)
            elif cron_type == "interval":
                interval_s = job.get("interval_s") or 0
                next_run = now_local() + timedelta(seconds=max(interval_s, 1))
                await self._repo.update_next_run(job_id, next_run)
            elif cron_type == "cron":
                next_run = next_cron_run(job.get("cron_expr") or "")
                if next_run:
                    await self._repo.update_next_run(job_id, next_run)
                else:
                    # cron 表达式异常时降级为 mark_done，避免永久卡死
                    logger.warning("CronScheduler: cron_expr 解析失败，job={} 标记为 done", job_id)
                    await self._repo.mark_done(job_id)
        except Exception as exc:
            logger.error("CronScheduler: job={} 状态更新失败（已执行）: {}", job_id, exc)
        finally:
            self._running_sessions.discard(session_key)

    async def _enqueue_activation(self, job: dict) -> None:
        """Convert a due cron job into the unified system activation protocol."""
        from Mojing.runtime.activations import ActivationRequest

        job_id = str(job.get("id") or "").strip()
        tenant_key = str(job.get("tenant_key") or "").strip()
        session_key = str(job.get("session_key") or "").strip()
        task = str(job.get("task") or "").strip()
        run_at = str(job.get("run_at") or "").strip()
        if not tenant_key or not session_key or not job_id or not task:
            raise RuntimeError("cron job missing tenant/session/job/task")
        if self._activation_service is None:
            raise RuntimeError("activation service is not configured")

        await self._activation_service.enqueue(
            ActivationRequest(
                session_key=session_key,
                tenant_key=tenant_key,
                activation_kind="cron_due",
                task_id=job_id,
                source_type="cron",
                source_id=job_id,
                summary="用户设置的定时提醒已到期",
                reminder_text=_build_cron_trigger(task),
                priority="low",
                delivery_policy="must_run",
                preempt_policy="keep",
                dedupe_key=f"cron_due:{tenant_key}:{job_id}:{run_at}",
                persist_completion_event=False,
                payload_json={
                    "cron_type": job.get("cron_type"),
                    "cron_expr": job.get("cron_expr"),
                    "interval_s": job.get("interval_s"),
                    "run_at": run_at,
                    "task": task,
                },
            )
        )

    async def _execute_turn(self, tenant_key: str, session_key: str, message: str) -> None:
        """在目标 session 中运行一轮 ReactLoop，消耗所有事件但不推送 SSE。

        与主 Agent 共用同一把 session 锁，防止和用户正在进行的对话并发写入消息列表。
        """
        from simpleclaw.core.events import DoneEvent, ErrorEvent, TextEvent
        from simpleclaw.context import AttentionPacket

        # 持锁覆盖整个 turn（get_or_create → run → save_turn），与主 Agent 行为一致
        session_lock = self._sessions.get_lock(session_key)
        async with session_lock:
            loop = await self._sessions.get_or_create(session_key, tenant_key)
            messages_before = loop.absolute_message_count
            # MainAgent 的业务上下文由 ContextBuilder providers 每轮收集；
            # 这里只追加 cron 触发本身这个一次性 attention。
            attention_packets = [
                AttentionPacket(
                    content=_build_cron_trigger(message),
                    source="cron_trigger",
                    priority=5,
                    placement="tail",
                )
            ]

            self._sessions.set_turn_context(
                session_key,
                tenant_key=tenant_key,
                query=message,
            )

            reply_parts: list[str] = []
            try:
                async for event in loop.run(
                    "",
                    query=message,
                    persist_user_input=False,
                    attention_packets=attention_packets,
                    context_metadata={
                        "tenant_key": tenant_key,
                        "session_key": session_key,
                        "entrypoint": "cron",
                        "image_just_uploaded": False,
                    },
                ):
                    if isinstance(event, TextEvent):
                        reply_parts.append(event.token)
                        await self._publish(
                            tenant_key,
                            session_key,
                            {
                                "type": "chunk",
                                "source": "cron",
                                "text": event.token,
                            },
                        )
                    elif isinstance(event, DoneEvent):
                        await self._publish(
                            tenant_key,
                            session_key,
                            {
                                "type": "done",
                                "source": "cron",
                            },
                        )
                        break
                    elif isinstance(event, ErrorEvent):
                        logger.warning("CronScheduler: ReactLoop 错误: {}", event.message)
                        await self._publish(
                            tenant_key,
                            session_key,
                            {
                                "type": "error",
                                "source": "cron",
                                "error": event.message,
                            },
                        )
                        break
            finally:
                try:
                    await self._sessions.save_turn(session_key, tenant_key, messages_before)
                except Exception as e:
                    logger.warning("CronScheduler: save_turn 失败: {}", e)

        logger.info(
            "CronScheduler: job 执行完毕 tenant={} session={} reply_len={}",
            tenant_key, session_key, len("".join(reply_parts)),
        )

    async def _publish(self, tenant_key: str, session_key: str, event: dict) -> None:
        if self._publish_fn is None:
            return
        try:
            delivered = await self._publish_fn(tenant_key, session_key, event)
            logger.debug(
                "CronScheduler: publish source={} type={} tenant={} session={} delivered={}",
                event.get("source"), event.get("type"), tenant_key, session_key, delivered,
            )
        except Exception as exc:
            logger.warning("CronScheduler: publish failed tenant={} session={} err={}", tenant_key, session_key, exc)


def _build_cron_trigger(task: str) -> str:
    """构造本轮临时系统触发语，不写入会话历史。"""
    return (
        "【定时任务触发】这不是用户刚刚发来的新消息，也不要在回复里提及定时器、调度器、"
        "cron 或“你刚刚设置了提醒”。现在到了约定时间，请根据下面的任务，主动自然地给用户发一条消息：\n"
        f"{task}"
    )
