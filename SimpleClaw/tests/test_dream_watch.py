"""Tests for scenario runner dream watch hooks (no MySQL required)."""
from __future__ import annotations

import unittest

from Mojing.storage.dream_repo import DreamRepository
from Mojing.storage.memory_ledger_repo import MemoryLedgerRepository
from script.runner.dream_watch import DreamWatcher


class _FakeLogger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, message: str) -> None:
        self.lines.append(message)


class _LedgerRecord:
    def __init__(self, ledger_id, tenant_key, status, dream_status, metadata):
        self.ledger_id = ledger_id
        self.tenant_key = tenant_key
        self.status = status
        self.dream_status = dream_status
        self.metadata = metadata


class TestDreamWatch(unittest.IsolatedAsyncioTestCase):
    async def test_ledger_update_logs_lifecycle_and_guardrail(self) -> None:
        log = _FakeLogger()
        watcher = DreamWatcher(log, tenant_key="test_ab12")
        watcher.set_phase("2")

        async def fake_update_ledger(self, ledger_id, **kwargs):
            return _LedgerRecord(
                ledger_id, "test_ab12",
                kwargs.get("status") or "applied",
                kwargs.get("dream_status") or "pending",
                {"guardrail": {"verdict": "reject_line", "rejected": ["T区毛孔"], "checked_lines": 3}},
            )

        original = MemoryLedgerRepository.update_ledger
        MemoryLedgerRepository.update_ledger = fake_update_ledger
        try:
            watcher.install()
            repo = MemoryLedgerRepository.__new__(MemoryLedgerRepository)
            await repo.update_ledger("memledger_x", status="applied", dream_status="pending")
        finally:
            watcher.uninstall()
            MemoryLedgerRepository.update_ledger = original

        joined = "\n".join(log.lines)
        self.assertIn("LEDGER turn=2 ledger=memledger_x", joined)
        self.assertIn("status=applied", joined)
        self.assertIn("GUARDRAIL turn=2 verdict=reject_line", joined)

    async def test_other_tenant_is_ignored(self) -> None:
        log = _FakeLogger()
        watcher = DreamWatcher(log, tenant_key="test_ab12")

        async def fake_update_ledger(self, ledger_id, **kwargs):
            return _LedgerRecord(ledger_id, "test_other", "applied", "pending", {})

        original = MemoryLedgerRepository.update_ledger
        MemoryLedgerRepository.update_ledger = fake_update_ledger
        try:
            watcher.install()
            repo = MemoryLedgerRepository.__new__(MemoryLedgerRepository)
            await repo.update_ledger("memledger_y", status="applied")
        finally:
            watcher.uninstall()
            MemoryLedgerRepository.update_ledger = original

        self.assertEqual(log.lines, [])


if __name__ == "__main__":
    unittest.main()
