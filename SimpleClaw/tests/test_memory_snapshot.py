# tests/test_memory_snapshot.py
"""终态快照渲染纯函数单测。"""
from __future__ import annotations

import unittest

from script.runner.memory_snapshot import render_memory_snapshot_md


class TestRenderMemorySnapshot(unittest.TestCase):
    def test_renders_two_sections(self) -> None:
        md = render_memory_snapshot_md(
            scenario_id="MEM05_x",
            tenant_key="test_ab12",
            entries=[
                {"topic": "毛孔与黑头问题", "description": "鼻翼黑头先加重后改善",
                 "content": "5.20 轻度 / 5.26 重度", "source": "main", "is_skin": True},
                {"topic": "用户情绪", "description": "焦虑", "content": "",
                 "source": "main", "is_skin": False},
            ],
            ledgers=[
                {"ledger_id": "m1", "status": "applied", "dream_status": "reviewed",
                 "guardrail": {"verdict": "accept", "rejected": [], "checked_lines": 3}},
            ],
            artifacts=[
                {"artifact_key": "memory-ledger:m1", "status": "applied",
                 "applied": True, "content": "毛孔与黑头问题 …"},
            ],
        )
        self.assertIn("# Memory 终态 — MEM05_x", md)
        self.assertIn("## 1. 最终记忆条目", md)
        self.assertIn("[skin] topic=毛孔与黑头问题", md)
        self.assertIn("[—] topic=用户情绪", md)
        self.assertIn("## 2. Dream 操作", md)
        self.assertIn("ledger m1", md)
        self.assertIn("verdict=accept", md)
        self.assertIn("status=applied", md)

    def test_empty(self) -> None:
        md = render_memory_snapshot_md(
            scenario_id="s", tenant_key="t", entries=[], ledgers=[], artifacts=[],
        )
        self.assertIn("（无记忆条目）", md)
        self.assertIn("（无 dream 操作）", md)


if __name__ == "__main__":
    unittest.main()
