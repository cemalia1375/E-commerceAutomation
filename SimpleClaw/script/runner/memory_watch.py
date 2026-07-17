"""Memory 写入监控：场景测试中实时捕获 nb_memory_entries 的写入。

通过 class-level monkeypatch 包装 MySQLMemory.store/delete，
每次写入按"当前 turn 指针"归属，并立刻写入 {scenario_id}.log。
参考 capture.wrap_all_tool_registries 的 patch + restore 风格；
卸载后类方法恢复原引用，同进程多场景不串扰。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

_EXISTS_SQL = (
    "SELECT 1 FROM nb_memory_entries "
    "WHERE tenant_key = %s AND source = %s AND topic = %s LIMIT 1"
)


@dataclass
class MemoryWriteRecord:
    phase: str                      # "1" / "3" / "after_turns" / "2:scenario_action"
    action: str                     # create / update / delete / store(降级)
    source: str | None = None       # main / skin_diary / deep_report
    topic: str | None = None
    description: str | None = None


class MemoryWatcher:
    """实时捕获 memory 写入并按 turn 归属打日志。"""

    def __init__(self, logger: Any, tenant_key: str) -> None:
        self._log = logger
        self._tenant_key = tenant_key
        self._phase = "pre"
        self.records: list[MemoryWriteRecord] = []
        self._restores: list[Callable[[], None]] = []

    def set_phase(self, phase: str) -> None:
        """更新当前 turn 指针；runner 在每个 turn 开始时调用。"""
        self._phase = phase

    # ------------------------------------------------------------------
    # 安装 / 卸载
    # ------------------------------------------------------------------

    def install(self) -> None:
        from Mojing.storage.memory_repo import MySQLMemory

        watcher = self
        original_store = MySQLMemory.store
        original_delete = MySQLMemory.delete

        async def wrapped_store(mem_self, key, content, *, description="", metadata=None, **kwargs):
            # **kwargs 透传 memory_type 等新增参数；少一个就 TypeError → 上游吞掉 → 记忆写不进库
            if mem_self._tenant_key != watcher._tenant_key:
                return await original_store(
                    mem_self, key, content, description=description, metadata=metadata, **kwargs
                )
            action = await watcher._classify_store(mem_self, key)
            await original_store(
                mem_self, key, content, description=description, metadata=metadata, **kwargs
            )
            watcher._record(
                MemoryWriteRecord(
                    phase=watcher._phase,
                    action=action,
                    source=mem_self._source,
                    topic=key,
                    description=description,
                )
            )

        async def wrapped_delete(mem_self, key):
            if mem_self._tenant_key != watcher._tenant_key:
                return await original_delete(mem_self, key)
            await original_delete(mem_self, key)
            watcher._record(
                MemoryWriteRecord(
                    phase=watcher._phase,
                    action="delete",
                    source=mem_self._source,
                    topic=key,
                )
            )

        MySQLMemory.store = wrapped_store
        MySQLMemory.delete = wrapped_delete
        self._restores = [
            lambda: setattr(MySQLMemory, "store", original_store),
            lambda: setattr(MySQLMemory, "delete", original_delete),
        ]

    def uninstall(self) -> None:
        for restore in reversed(self._restores):
            restore()
        self._restores = []

    # ------------------------------------------------------------------
    # 记录 / 总结
    # ------------------------------------------------------------------

    def summary_line(self) -> str:
        turns = sorted({r.phase for r in self.records}, key=_phase_sort_key)
        return f"MEMORY SUMMARY total={len(self.records)} turns=[{', '.join(turns)}]"

    async def _classify_store(self, mem_self: Any, key: str) -> str:
        """用一条存在性 SELECT 区分 create/update；查询失败降级为 store。"""
        try:
            async with mem_self._db.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        _EXISTS_SQL,
                        (mem_self._tenant_key, mem_self._source, key),
                    )
                    row = await cur.fetchone()
            return "update" if row else "create"
        except Exception:
            return "store"

    def _record(self, record: MemoryWriteRecord) -> None:
        self.records.append(record)
        desc = f" desc={record.description}" if record.description else ""
        self._log.write(
            f"MEMORY turn={record.phase} action={record.action} "
            f"source={record.source} topic={record.topic}{desc}"
        )


def _phase_sort_key(phase: str) -> tuple:
    head = phase.split(":", 1)[0]
    if head.isdigit():
        return (0, int(head), phase)
    return (1, 0, phase)


def install_memory_watch(logger: Any, *, tenant_key: str) -> MemoryWatcher:
    """创建并安装 MemoryWatcher；调用方负责在 finally 中 uninstall()。"""
    watcher = MemoryWatcher(logger, tenant_key)
    watcher.install()
    return watcher
