"""任务队列抽象 — InMemory（开发环境）和 Redis Streams（生产环境）。

使用方式：
    # 开发环境（无 Redis）
    queue = InMemoryTaskQueue()

    # 生产环境
    queue = RedisTaskQueue(url="redis://127.0.0.1:6379/0", stream_prefix="app:tasks")

    # 生产者
    await queue.enqueue(TaskEnvelope(task_type="example", stream="example_stream", ...))

    # 消费者（在 worker loop 中）
    messages = await queue.consume("example_stream", consumer_group="app", consumer_name="w1")
    for msg in messages:
        await process(msg.task)
        await queue.ack(msg, consumer_group="mojing")
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass

from loguru import logger

from simpleclaw.runtime.task_protocol import TaskEnvelope, TaskStream


@dataclass(slots=True)
class TaskMessage:
    """从队列消费到的一条消息。"""

    stream: TaskStream
    queue_id: str
    task: TaskEnvelope


class InMemoryTaskQueue:
    """单进程内存队列，开发/测试用。不支持持久化和重试。"""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[TaskMessage]] = defaultdict(asyncio.Queue)
        self._counter = 0

    async def enqueue(self, task: TaskEnvelope) -> str:
        self._counter += 1
        queue_id = f"mem-{self._counter}"
        await self._queues[task.stream].put(
            TaskMessage(stream=task.stream, queue_id=queue_id, task=task)
        )
        return queue_id

    async def consume(
        self,
        stream: TaskStream,
        *,
        consumer_group: str,
        consumer_name: str,
        count: int = 1,
    ) -> list[TaskMessage]:
        del consumer_group, consumer_name
        queue = self._queues[stream]
        first = await queue.get()          # 阻塞等待
        items = [first]
        while len(items) < count and not queue.empty():
            items.append(queue.get_nowait())
        return items

    async def ack(self, message: TaskMessage, *, consumer_group: str) -> None:
        del message, consumer_group        # 内存队列无需 ack


class RedisTaskQueue:
    """Redis Streams 队列，生产环境使用。

    需要安装 redis[asyncio] 依赖：pip install 'redis[asyncio]'
    """

    def __init__(
        self,
        *,
        url: str,
        stream_prefix: str = "mojing:tasks",
        block_ms: int = 5000,
        batch_size: int = 8,
        claim_min_idle_ms: int = 60_000,
    ) -> None:
        self._url = url
        self._stream_prefix = stream_prefix.rstrip(":")
        self._block_ms = max(100, block_ms)
        self._batch_size = max(1, batch_size)
        self._claim_min_idle_ms = max(0, int(claim_min_idle_ms or 0))
        self._socket_timeout_s = max(self._block_ms / 1000.0 + 5.0, 10.0)
        self._socket_connect_timeout_s = 5.0
        self._client = None

    async def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from redis import asyncio as aioredis
        except ImportError as exc:
            raise RuntimeError(
                "Redis 支持需要安装 redis 包：pip install 'redis[asyncio]'"
            ) from exc
        self._client = aioredis.from_url(
            self._url,
            decode_responses=True,
            socket_timeout=self._socket_timeout_s,
            socket_connect_timeout=self._socket_connect_timeout_s,
            retry_on_timeout=True,
        )
        return self._client

    def _stream_name(self, stream: TaskStream) -> str:
        return f"{self._stream_prefix}:{stream}"

    async def _ensure_group(self, stream: TaskStream, consumer_group: str) -> None:
        client = await self._get_client()
        try:
            await client.xgroup_create(
                name=self._stream_name(stream),
                groupname=consumer_group,
                id="0",
                mkstream=True,
            )
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def enqueue(self, task: TaskEnvelope) -> str:
        client = await self._get_client()
        msg_id = await client.xadd(
            self._stream_name(task.stream),
            {"payload": task.to_json()},
        )
        logger.debug(
            "task_queue.enqueue: stream={} type={} task_id={} redis_id={}",
            task.stream, task.task_type, task.task_id, msg_id,
        )
        return msg_id

    async def consume(
        self,
        stream: TaskStream,
        *,
        consumer_group: str,
        consumer_name: str,
        count: int = 1,
    ) -> list[TaskMessage]:
        await self._ensure_group(stream, consumer_group)
        client = await self._get_client()
        batch_count = max(1, min(count, self._batch_size))

        reclaimed = await self._consume_stale_pending(
            stream,
            consumer_group=consumer_group,
            consumer_name=consumer_name,
            count=batch_count,
        )
        if reclaimed:
            return reclaimed

        raw = await client.xreadgroup(
            groupname=consumer_group,
            consumername=consumer_name,
            streams={self._stream_name(stream): ">"},
            count=batch_count,
            block=self._block_ms,
        )
        if not raw:
            return []

        items: list[TaskMessage] = []
        for _stream_name, entries in raw:
            items.extend(self._deserialize_entries(stream, entries))
        return items

    async def _consume_stale_pending(
        self,
        stream: TaskStream,
        *,
        consumer_group: str,
        consumer_name: str,
        count: int,
    ) -> list[TaskMessage]:
        if self._claim_min_idle_ms <= 0:
            return []

        client = await self._get_client()
        try:
            raw = await client.xautoclaim(
                name=self._stream_name(stream),
                groupname=consumer_group,
                consumername=consumer_name,
                min_idle_time=self._claim_min_idle_ms,
                start_id="0-0",
                count=count,
            )
        except Exception as exc:
            logger.warning("task_queue.autoclaim failed: stream={} err={}", stream, exc)
            return []

        entries = _extract_xautoclaim_entries(raw)
        if not entries:
            return []

        items = self._deserialize_entries(stream, entries)
        logger.info(
            "task_queue.autoclaim: stream={} consumer={} claimed={}",
            stream,
            consumer_name,
            len(items),
        )
        return items

    def _deserialize_entries(
        self,
        stream: TaskStream,
        entries: list[tuple[str, dict[str, str]]],
    ) -> list[TaskMessage]:
        items: list[TaskMessage] = []
        for queue_id, fields in entries:
            payload = fields.get("payload")
            if not payload:
                logger.warning("task_queue.consume: empty payload, skipping")
                continue
            try:
                task = TaskEnvelope.from_json(payload)
            except Exception as exc:
                logger.error("task_queue.consume: deserialize failed: {}", exc)
                continue
            items.append(TaskMessage(stream=stream, queue_id=queue_id, task=task))
        return items

    async def ack(self, message: TaskMessage, *, consumer_group: str) -> None:
        client = await self._get_client()
        await client.xack(
            self._stream_name(message.stream),
            consumer_group,
            message.queue_id,
        )


def _extract_xautoclaim_entries(raw) -> list[tuple[str, dict[str, str]]]:
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        if len(raw) >= 2 and isinstance(raw[1], list):
            return raw[1]
        if raw and isinstance(raw[0], tuple) and len(raw[0]) == 2:
            return list(raw)
    return []
