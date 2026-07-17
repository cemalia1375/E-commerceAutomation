"""AppContainer 必须暴露 dream_scheduler / memory_ledger_repo 供场景 runner 访问。"""
from __future__ import annotations

import unittest

from Mojing.api.container import AppContainer


class TestContainerExposesDream(unittest.TestCase):
    def test_dream_scheduler_field_present(self) -> None:
        self.assertIn("dream_scheduler", AppContainer.__annotations__)

    def test_memory_ledger_repo_field_present(self) -> None:
        self.assertIn("memory_ledger_repo", AppContainer.__annotations__)


if __name__ == "__main__":
    unittest.main()
