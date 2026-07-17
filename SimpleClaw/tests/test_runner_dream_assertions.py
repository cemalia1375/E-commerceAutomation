# tests/test_runner_dream_assertions.py
"""Tests for memory/dream assertion checks."""
from __future__ import annotations

import unittest

from script.runner.assertions import _eval_named


def _turn(**over):
    base = {
        "reply": "",
        "tools_called": [],
        "memory_entries": [],
        "memory_ledgers": [],
        "dream_artifacts": [],
    }
    base.update(over)
    return base


class TestDreamAssertions(unittest.TestCase):
    def test_memory_entry_exists_skin(self) -> None:
        turn = _turn(memory_entries=[
            {"topic": "毛孔与黑头问题", "description": "鼻翼黑头先加重后改善",
             "content": "", "source": "main", "is_skin": True},
        ])
        r = _eval_named("memory_entry_exists",
                        {"topic_contains": "黑头", "skin_only": True,
                         "description_contains": "先加重后改善"}, turn)
        self.assertTrue(r.passed, r.detail)

    def test_memory_entry_exists_missing(self) -> None:
        r = _eval_named("memory_entry_exists", {"topic_contains": "黑头"}, _turn())
        self.assertFalse(r.passed)

    def test_memory_entry_applied(self) -> None:
        turn = _turn(dream_artifacts=[
            {"artifact_key": "memory-ledger:m1", "status": "applied",
             "applied": True, "content": "毛孔与黑头问题 鼻翼黑头先加重后改善"},
        ])
        r = _eval_named("memory_entry_applied", {"topic_contains": "黑头"}, turn)
        self.assertTrue(r.passed, r.detail)

    def test_memory_entry_draft_only(self) -> None:
        turn = _turn(dream_artifacts=[
            {"artifact_key": "memory-ledger:m2", "status": "draft",
             "applied": False, "content": "用户情绪 焦虑"},
        ])
        r = _eval_named("memory_entry_draft_only", {"topic_contains": "用户情绪"}, turn)
        self.assertTrue(r.passed, r.detail)

    def test_memory_entry_draft_only_fails_if_applied(self) -> None:
        turn = _turn(dream_artifacts=[
            {"artifact_key": "memory-ledger:m3", "status": "applied",
             "applied": True, "content": "用户情绪 焦虑"},
        ])
        r = _eval_named("memory_entry_draft_only", {"topic_contains": "用户情绪"}, turn)
        self.assertFalse(r.passed)

    def test_guardrail_verdict_absent(self) -> None:
        r = _eval_named("guardrail_verdict", "absent", _turn())
        self.assertTrue(r.passed, r.detail)

    def test_guardrail_verdict_match(self) -> None:
        turn = _turn(memory_ledgers=[
            {"ledger_id": "m1", "status": "applied", "dream_status": "pending",
             "guardrail": {"verdict": "accept", "rejected": [], "checked_lines": 3}},
        ])
        r = _eval_named("guardrail_verdict", "accept", turn)
        self.assertTrue(r.passed, r.detail)


if __name__ == "__main__":
    unittest.main()
