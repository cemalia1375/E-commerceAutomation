"""Focused tests for backend session propagation into Agent session keys."""

from __future__ import annotations

import unittest

from Mojing.api.request_utils import resolve_agent_chat_context


class BackendSessionPropagationTest(unittest.TestCase):
    def test_agent_chat_context_uses_backend_session_id_as_main_session(self) -> None:
        ctx = resolve_agent_chat_context(
            {
                "user_id": "290",
                "backendSessionId": "session_290_1770106399774_U992Dj",
                "message": "继续上一轮",
            }
        )

        self.assertEqual(ctx["tenant_key"], "290")
        self.assertEqual(ctx["session_key"], "main:session_290_1770106399774_U992Dj")
        self.assertEqual(ctx["origin_session_key"], "main:session_290_1770106399774_U992Dj")


if __name__ == "__main__":
    unittest.main()
