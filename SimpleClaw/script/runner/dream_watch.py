"""Dream 写入监控：场景测试中实时捕获 ledger 生命周期、dream job、护栏 verdict。

与 memory_watch.py 对称：class-level monkeypatch 包
MemoryLedgerRepository.update_ledger 与 DreamRepository.update_job_status /
save_artifacts，按当前 turn 指针归属，立刻写入 {scenario_id}.log。
按 tenant 过滤；卸载后类方法恢复原引用，同进程多场景不串扰。
"""
from __future__ import annotations

from typing import Any, Callable


class DreamWatcher:
    """实时捕获 ledger / dream / 护栏并按 turn 归属打日志。"""

    def __init__(self, logger: Any, tenant_key: str) -> None:
        self._log = logger
        self._tenant_key = tenant_key
        self._phase = "pre"
        self._restores: list[Callable[[], None]] = []

    def set_phase(self, phase: str) -> None:
        """更新当前 turn 指针；runner 在每个 turn / run_dream_now 时调用。"""
        self._phase = phase

    # ------------------------------------------------------------------
    # 安装 / 卸载
    # ------------------------------------------------------------------

    def install(self) -> None:
        from Mojing.storage.dream_repo import DreamRepository
        from Mojing.storage.memory_ledger_repo import MemoryLedgerRepository

        watcher = self
        original_update_ledger = MemoryLedgerRepository.update_ledger
        original_update_job = DreamRepository.update_job_status
        original_save_artifacts = DreamRepository.save_artifacts

        async def wrapped_update_ledger(repo_self, ledger_id, **kwargs):
            record = await original_update_ledger(repo_self, ledger_id, **kwargs)
            if record is not None and getattr(record, "tenant_key", None) == watcher._tenant_key:
                watcher._log_ledger(record)
            return record

        async def wrapped_update_job(repo_self, job_id, status, **kwargs):
            await original_update_job(repo_self, job_id, status, **kwargs)
            job = await repo_self.get_job(job_id)
            if job is not None and getattr(job, "tenant_key", None) == watcher._tenant_key:
                watcher._log.write(
                    f"DREAM turn={watcher._phase} job={job_id} status={status} "
                    f"owner={getattr(job, 'source_id', None)}"
                )

        async def wrapped_save_artifacts(repo_self, artifacts):
            await original_save_artifacts(repo_self, artifacts)
            for art in artifacts:
                if getattr(art, "tenant_key", None) != watcher._tenant_key:
                    continue
                watcher._log.write(
                    f"DREAM turn={watcher._phase} artifact={getattr(art, 'artifact_key', None)} "
                    f"status={getattr(art, 'status', None)}"
                )

        MemoryLedgerRepository.update_ledger = wrapped_update_ledger
        DreamRepository.update_job_status = wrapped_update_job
        DreamRepository.save_artifacts = wrapped_save_artifacts
        self._restores = [
            lambda: setattr(MemoryLedgerRepository, "update_ledger", original_update_ledger),
            lambda: setattr(DreamRepository, "update_job_status", original_update_job),
            lambda: setattr(DreamRepository, "save_artifacts", original_save_artifacts),
        ]

    def uninstall(self) -> None:
        for restore in reversed(self._restores):
            restore()
        self._restores = []

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------

    def _log_ledger(self, record: Any) -> None:
        self._log.write(
            f"LEDGER turn={self._phase} ledger={record.ledger_id} "
            f"status={getattr(record, 'status', None)} "
            f"dream_status={getattr(record, 'dream_status', None)}"
        )
        metadata = getattr(record, "metadata", None) or {}
        guardrail = metadata.get("guardrail") if isinstance(metadata, dict) else None
        if isinstance(guardrail, dict):
            self._log.write(
                f"GUARDRAIL turn={self._phase} verdict={guardrail.get('verdict')} "
                f"rejected={guardrail.get('rejected')} checked_lines={guardrail.get('checked_lines')}"
            )


def install_dream_watch(logger: Any, *, tenant_key: str) -> DreamWatcher:
    """创建并安装 DreamWatcher；调用方负责在 finally 中 uninstall()。"""
    watcher = DreamWatcher(logger, tenant_key)
    watcher.install()
    return watcher
