import unittest

from Mojing.dream.tools.write import UpsertMemoryEntryTool


class _StubCursor:
    def __init__(self):
        self.executed = []
    async def execute(self, sql, params=None):
        self.executed.append((sql, params))
    async def __aenter__(self):
        return self
    async def __aexit__(self, *exc):
        return False


class _StubConn:
    def __init__(self, cur):
        self._cur = cur
    def cursor(self):
        return self._cur
    async def __aenter__(self):
        return self
    async def __aexit__(self, *exc):
        return False


class _StubAcquire:
    def __init__(self, conn):
        self._conn = conn
    async def __aenter__(self):
        return self._conn
    async def __aexit__(self, *exc):
        return False


class _StubDB:
    def __init__(self):
        self.cur = _StubCursor()
    def acquire(self):
        return _StubAcquire(_StubConn(self.cur))


def _ok(result) -> bool:
    # 兼容 ToolResult 结构：优先 .ok，否则解析 payload 里的 "ok"
    if hasattr(result, "ok"):
        return bool(result.ok)
    import json
    text = getattr(result, "content", None) or getattr(result, "output", None) or str(result)
    try:
        return bool(json.loads(text).get("ok"))
    except Exception:
        return "\"ok\": true" in str(text) or "'ok': True" in str(text)


class TestDreamSkinApply(unittest.IsolatedAsyncioTestCase):
    async def test_skin_apply_allowed_when_mutation_off(self) -> None:
        db = _StubDB()
        tool = UpsertMemoryEntryTool(
            db=db, tenant_key="prod_user", allowed=False,
            skin_apply_allowed=True, job_id="job1",
        )
        result = await tool.execute(
            topic="皮肤问题", description="鼻翼黑头先加重后改善",
            content="黑头（鼻翼）：\n 5.20 轻度", reason="merge 多行",
            memory_type="skin",
        )
        self.assertTrue(_ok(result))
        self.assertTrue(db.cur.executed)  # 真写库

    async def test_chitchat_still_draft_only_when_mutation_off(self) -> None:
        db = _StubDB()
        tool = UpsertMemoryEntryTool(
            db=db, tenant_key="prod_user", allowed=False,
            skin_apply_allowed=True, job_id="job1",
        )
        result = await tool.execute(
            topic="工作受委屈", description="x", content="y", reason="z",
            memory_type="chitchat",
        )
        self.assertFalse(_ok(result))
        self.assertFalse(db.cur.executed)  # 未写库

    async def test_full_mutation_allows_chitchat(self) -> None:
        db = _StubDB()
        tool = UpsertMemoryEntryTool(
            db=db, tenant_key="test_x", allowed=True,
            skin_apply_allowed=False, job_id="j",
        )
        result = await tool.execute(
            topic="日常闲聊", description="聊天记录", content="内容详情", reason="测试租户可写任意类型",
            memory_type="chitchat",
        )
        self.assertTrue(_ok(result))
        self.assertTrue(db.cur.executed)  # test 租户可写任意类型

    async def test_skin_write_to_nonmain_source_blocked(self) -> None:
        db = _StubDB()
        tool = UpsertMemoryEntryTool(
            db=db, tenant_key="prod_user", allowed=False,
            skin_apply_allowed=True, job_id="j",
        )
        result = await tool.execute(
            topic="皮肤问题", description="描述", content="内容", reason="来源非main",
            memory_type="skin", source="some_other",
        )
        self.assertFalse(_ok(result))
        self.assertFalse(db.cur.executed)  # 非 main source 必须阻断

    async def test_skin_write_to_subagent_default_source_blocked(self) -> None:
        # 即便 ledger 派生的 default_source 非 main，skin 免授权 apply 也只允许写 source='main'
        db = _StubDB()
        tool = UpsertMemoryEntryTool(
            db=db, tenant_key="prod_user", allowed=False,
            skin_apply_allowed=True, job_id="j", default_source="skin_diary",
        )
        result = await tool.execute(
            topic="皮肤问题", description="描述", content="内容", reason="default_source 非 main",
            memory_type="skin", source="skin_diary",
        )
        self.assertFalse(_ok(result))
        self.assertFalse(db.cur.executed)


class TestDreamRunnerTrend(unittest.IsolatedAsyncioTestCase):
    async def test_runner_accepts_skin_profile_repo(self) -> None:
        from Mojing.dream.subagent_runner import MojingDreamSubagentRunner

        class _P:
            async def list_profiles_in_range(self, *a, **k):
                return []

        runner = MojingDreamSubagentRunner(
            db=_StubDB(),
            memory_ledger_repo=None,
            session_repo=None,
            document_repo=None,
            runtime_task_repo=None,
            llm=None,
            skin_profile_repo=_P(),
        )
        self.assertIsNotNone(runner)


if __name__ == "__main__":
    unittest.main()
