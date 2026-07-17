# tests/test_run_dream_now_helper.py
"""run_dream_now 的纯判定 helper 单测。"""
from __future__ import annotations

import unittest

from script.runner.runner import _dream_force_allowed


class TestDreamForceAllowed(unittest.TestCase):
    def test_test_tenant_with_bypass(self) -> None:
        self.assertTrue(_dream_force_allowed("test_ab12", bypass_cooldown=True))

    def test_prod_tenant_bypass_denied(self) -> None:
        self.assertFalse(_dream_force_allowed("user_290", bypass_cooldown=True))

    def test_bypass_off(self) -> None:
        self.assertFalse(_dream_force_allowed("test_ab12", bypass_cooldown=False))


if __name__ == "__main__":
    unittest.main()
