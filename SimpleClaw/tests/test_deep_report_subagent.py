"""Unit tests for DeepReportSubagent V2 (reportId + 三表 JOIN + fallback)."""

from __future__ import annotations

import unittest
from typing import Any

from Mojing.subagent.deep_report import (
    DeepReportSubagent,
)


def _build_full_report(report_id: str = "RPT-1") -> dict[str, Any]:
    """构造一份三表都齐全的 dict，模拟 DeepReportRepository._fetch_one_full 输出。"""
    return {
        "report_id": report_id,
        "user_id": "tenant-A",
        "session_id": "sess-1",
        "status": "done",
        "strategy_version": "v1.2",
        "summary": "整体稳定，匀净度需留意。",
        "create_time": "2026-04-21 10:30:00",
        "update_time": "2026-04-21 10:31:00",
        "slow_overview": {
            "radarDimensions": [
                {
                    "dimensionCode": "tone_evenness",
                    "name": "匀净度",
                    "desc": "左面颊有轻度色斑",
                    "coreMetric": "肤色全域均匀度",
                    "level": "需留意",
                },
            ],
            "signal": {
                "locationLabels": [{"label": "色斑", "area": "左面颊"}],
            },
            "skinAttribute": {"oilType": "混合性肌肤", "stage": "轻熟肌"},
        },
        "slow_decode": {
            "signals": [
                {
                    "name": "色斑",
                    "tags": ["色素沉淀"],
                    "areas": ["左面颊"],
                    "locationText": "左面颊",
                    "images": [{"url": "http://cdn/o.jpg", "label": "原图"}],
                }
            ]
        },
        "slow_secret": {"focusTags": [{"label": "成为大美女"}]},  # 聚合规则下不渲染
        "slow_track": {
            "signalItems": [
                {"name": "色斑", "status": "需留意", "trend": "stable"},
            ]
        },
        "deep_overview": None,
        "deep_decode": {
            "signals": [
                {
                    "name": "色斑",
                    "chineseAnalysis": "肝气郁结、气滞血瘀。",
                    "westernAnalysis": "黑色素细胞过度活跃。",
                    "formula": "烟酰胺 + 防晒。",
                    "references": [{"title": "Melasma review", "source": "JAAD"}],
                }
            ]
        },
        "deep_secret": {
            "morningSteps": [
                {"order": 1, "title": "温和洁面", "usage": "温水冲洗", "effect": "清洁"}
            ],
            "eveningSteps": [
                {"order": 1, "title": "卸妆油", "usage": "干手干脸打圈", "effect": "彻底清洁"}
            ],
            "internalSteps": [{"order": 1, "title": "多喝水", "usage": "1500ml", "effect": "保湿"}],
        },
        "deep_track": None,
        "agent_overview": {
            "radarDimensions": [
                {
                    "dimensionCode": "tone_evenness",
                    "score": 38,
                    "status": "需留意",
                    "statusType": "attention",
                }
            ],
            "introText": "魔镜发现你的整体肌肤能量还不错呢~",
            "signal": {"tags": ["成为大美女"], "count": 1, "footer": "..."},
            "skinAttribute": {"introText": "皮肤很有层次感"},
        },
        "agent_decode": {
            "bannerText": "护肤 banner",
            "signals": [
                {
                    "name": "色斑",
                    "aiAnalysis": "我看到左面颊有淡淡的色斑。",
                    "analysisImages": [
                        {"url": "http://cdn/crop.jpg", "label": "左面颊"}
                    ],
                }
            ],
        },
        "agent_secret": {
            "introText": "专属护肤魔法密语已生成 ✨",
            "focusTags": [{"label": "成为大美女"}],
            "morningTitle": "晨间轻护维稳✨",
            "eveningTitle": "晚间专属修复时光✨",
            "internalTitle": "护肤内外兼修✨",
        },
        "agent_track": {
            "dayProgress": {"dayLabel": "Day 3", "milestone": "成长记录", "date": "2026-04-21"}
        },
    }


class _FakeReportRepo:
    """假 DeepReportRepository：仅实现 V2 子 Agent 用到的两个方法。"""

    def __init__(
        self,
        *,
        by_id_map: dict[str, dict[str, Any]] | None = None,
        latest_map: dict[str, dict[str, Any]] | None = None,
        raise_on_by_id: bool = False,
        raise_on_latest: bool = False,
    ) -> None:
        self._by_id = by_id_map or {}
        self._latest = latest_map or {}
        self._raise_on_by_id = raise_on_by_id
        self._raise_on_latest = raise_on_latest
        self.calls: list[tuple[str, str | None]] = []

    async def find_by_report_id_full(
        self, tenant_key: str, report_id: str
    ) -> dict[str, Any] | None:
        self.calls.append(("by_id", report_id))
        if self._raise_on_by_id:
            raise RuntimeError("simulated DB failure on by_id")
        # 双条件 user_id + report_id：模拟仓库的 WHERE s.user_id=%s AND s.report_id=%s
        hit = self._by_id.get(report_id)
        if hit is None:
            return None
        if hit.get("user_id") != tenant_key:
            return None  # 跨用户被静默过滤
        return hit

    async def find_latest_full(self, tenant_key: str) -> dict[str, Any] | None:
        self.calls.append(("latest", None))
        if self._raise_on_latest:
            raise RuntimeError("simulated DB failure on latest")
        return self._latest.get(tenant_key)


class _FakeDocumentRepo:
    def __init__(self, docs: dict[tuple[str, str], str] | None = None) -> None:
        self._docs = docs or {}

    async def get(self, tenant_key: str, doc_name: str) -> str | None:
        return self._docs.get((tenant_key, doc_name))


class _FakeRuntimeTaskRepo:
    def __init__(self, latest: dict[str, Any] | None = None) -> None:
        self._latest = latest
        self.calls: list[tuple[str, str]] = []

    async def find_latest_task_for(
        self,
        *,
        tenant_key: str,
        task_type: str,
    ) -> dict[str, Any] | None:
        self.calls.append((tenant_key, task_type))
        return self._latest


def _make_subagent(
    *,
    report_repo: _FakeReportRepo,
    document_repo: _FakeDocumentRepo | None = None,
    runtime_task_repo: _FakeRuntimeTaskRepo | None = None,
) -> DeepReportSubagent:
    """绕过 __init__ 的依赖装配，只注入 fetch_dynamic_context_sections 需要的属性。"""
    sub = DeepReportSubagent.__new__(DeepReportSubagent)
    sub._report_repo = report_repo  # type: ignore[attr-defined]
    sub._document_repo = document_repo or _FakeDocumentRepo()  # type: ignore[attr-defined]
    sub._runtime_task_repo = runtime_task_repo  # type: ignore[attr-defined]
    return sub


def _section_texts(sections) -> list[str]:
    return [section.content for section in sections]


def _join_sections(sections) -> str:
    return "\n".join(_section_texts(sections))


class DeepReportSubagentDynamicSectionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_report_id_renders_four_pagedata_sections(self) -> None:
        """Given 有效 reportId → 注入字符串包含五维评分/区域定位/护理步骤/track 段。"""
        report = _build_full_report("RPT-1")
        report["user_id"] = "tenant-A"
        repo = _FakeReportRepo(by_id_map={"RPT-1": report})
        sub = _make_subagent(report_repo=repo)

        sections = await sub.fetch_dynamic_context_sections("tenant-A", report_id="RPT-1")

        joined = _join_sections(sections)
        self.assertIn("【当前可用深度分析报告】", joined)
        self.assertIn("【五维状态】", joined)
        self.assertIn("【重点信号】", joined)
        self.assertIn("【长期护理方向】", joined)
        self.assertIn("匀净度", joined)  # slow 骨架字段保留
        self.assertIn("左面颊", joined)
        self.assertIn("晨间轻护维稳", joined)
        self.assertIn("晚间专属修复时光", joined)
        self.assertEqual(repo.calls, [("by_id", "RPT-1")])  # 命中后不再 fallback

    async def test_cross_tenant_report_id_does_not_fall_back_to_latest(self) -> None:
        """Given 跨 tenant reportId → 不返回他人内容，也不静默改读 latest。"""
        other_report = _build_full_report("RPT-OTHER")
        other_report["user_id"] = "tenant-B"
        own_latest = _build_full_report("RPT-LATEST")
        own_latest["user_id"] = "tenant-A"

        repo = _FakeReportRepo(
            by_id_map={"RPT-OTHER": other_report},
            latest_map={"tenant-A": own_latest},
        )
        sub = _make_subagent(report_repo=repo)

        sections = await sub.fetch_dynamic_context_sections("tenant-A", report_id="RPT-OTHER")

        joined = _join_sections(sections)
        self.assertNotIn("RPT-LATEST", joined)
        self.assertNotIn("RPT-OTHER", joined)
        self.assertEqual(repo.calls, [("by_id", "RPT-OTHER")])

    async def test_missing_report_id_uses_latest(self) -> None:
        """Given 没传 reportId（含主 Agent dispatch）→ 拉 latest。"""
        latest = _build_full_report("RPT-LATEST")
        latest["user_id"] = "tenant-A"
        repo = _FakeReportRepo(latest_map={"tenant-A": latest})
        sub = _make_subagent(report_repo=repo)

        sections = await sub.fetch_dynamic_context_sections("tenant-A")  # 无 report_id

        joined = _join_sections(sections)
        self.assertIn("RPT-LATEST", joined)
        self.assertEqual(repo.calls, [("latest", None)])

    async def test_no_report_returns_empty_context_without_report_id(self) -> None:
        """Given 用户无报告且没传 reportId → 不注入报告上下文。"""
        repo = _FakeReportRepo()  # 两层都返回 None
        sub = _make_subagent(report_repo=repo)

        sections = await sub.fetch_dynamic_context_sections("tenant-A")

        self.assertEqual(_section_texts(sections), [])
        self.assertNotIn("Overview", _join_sections(sections))

    async def test_missing_explicit_report_id_returns_empty_context(self) -> None:
        """Given 显式 reportId 未命中 → 不注入报告上下文，也不 fallback latest。"""
        latest = _build_full_report("RPT-LATEST")
        latest["user_id"] = "tenant-A"
        repo = _FakeReportRepo(latest_map={"tenant-A": latest})
        sub = _make_subagent(report_repo=repo)

        sections = await sub.fetch_dynamic_context_sections("tenant-A", report_id="X")

        joined = _join_sections(sections)
        self.assertEqual(_section_texts(sections), [])
        self.assertNotIn("RPT-LATEST", joined)
        self.assertEqual(repo.calls, [("by_id", "X")])

    async def test_runtime_pending_blocks_latest_report_without_report_id(self) -> None:
        """Given 最近 runtime task 仍在生成 → 不读取旧 latest 报告。"""
        latest = _build_full_report("RPT-OLD")
        latest["user_id"] = "tenant-A"
        repo = _FakeReportRepo(latest_map={"tenant-A": latest})
        runtime_repo = _FakeRuntimeTaskRepo(
            {
                "status": "wait_external",
                "updated_at": "2026-04-21 10:00:00",
            }
        )
        sub = _make_subagent(report_repo=repo, runtime_task_repo=runtime_repo)

        sections = await sub.fetch_dynamic_context_sections("tenant-A")
        joined = _join_sections(sections)
        self.assertNotIn("仍在生成中", joined)
        self.assertNotIn("RPT-OLD", joined)
        self.assertEqual(repo.calls, [])
        self.assertEqual(runtime_repo.calls, [("tenant-A", "deep_research")])

    async def test_runtime_failed_blocks_latest_report_without_report_id(self) -> None:
        """Given 最近 runtime task 失败 → 不读取旧 latest 报告。"""
        latest = _build_full_report("RPT-OLD")
        latest["user_id"] = "tenant-A"
        repo = _FakeReportRepo(latest_map={"tenant-A": latest})
        runtime_repo = _FakeRuntimeTaskRepo(
            {
                "status": "failed",
                "updated_at": "2026-04-21 10:00:00",
                "last_error": "timeout",
            }
        )
        sub = _make_subagent(report_repo=repo, runtime_task_repo=runtime_repo)

        sections = await sub.fetch_dynamic_context_sections("tenant-A")
        joined = _join_sections(sections)
        self.assertNotIn("生成失败", joined)
        self.assertNotIn("RPT-OLD", joined)
        self.assertEqual(repo.calls, [])

    async def test_runtime_succeeded_allows_latest_report_without_report_id(self) -> None:
        """Given 最近 runtime task 已成功 → 可以读取 latest 报告内容。"""
        latest = _build_full_report("RPT-LATEST")
        latest["user_id"] = "tenant-A"
        repo = _FakeReportRepo(latest_map={"tenant-A": latest})
        runtime_repo = _FakeRuntimeTaskRepo({"status": "succeeded"})
        sub = _make_subagent(report_repo=repo, runtime_task_repo=runtime_repo)

        sections = await sub.fetch_dynamic_context_sections("tenant-A")

        self.assertIn("RPT-LATEST", _join_sections(sections))
        self.assertEqual(repo.calls, [("latest", None)])

    async def test_empty_report_fields_are_skipped(self) -> None:
        """Given 报告字段空 → _is_empty 跳过该段，不渲染空 heading。"""
        report = {
            "report_id": "RPT-EMPTY",
            "user_id": "tenant-A",
            "create_time": "2026-04-21 10:30:00",
            # 4 段 JSON 全部为空
            "slow_overview": None,
            "slow_decode": {},
            "slow_secret": None,
            "slow_track": None,
            "deep_overview": None,
            "deep_decode": None,
            "deep_secret": {},
            "deep_track": None,
            "agent_overview": None,
            "agent_decode": None,
            "agent_secret": None,
            "agent_track": None,
        }
        repo = _FakeReportRepo(by_id_map={"RPT-EMPTY": report})
        sub = _make_subagent(report_repo=repo)

        sections = await sub.fetch_dynamic_context_sections("tenant-A", report_id="RPT-EMPTY")

        joined = _join_sections(sections)
        # 没有任何 h3 子段被渲染（不依赖具体中英文 heading 文案，更不易被无关重命名误伤）
        self.assertNotIn("###", joined)
        self.assertNotIn("Overview", joined)
        self.assertNotIn("Decode", joined)
        self.assertNotIn("Secret", joined)
        self.assertNotIn("Track", joined)
        # 但报告 ID 仍在
        self.assertIn("RPT-EMPTY", joined)

    async def test_db_failure_returns_empty_context(self) -> None:
        """Given latest DB 连接失败 → 不注入报告上下文，对话不阻断。"""
        repo = _FakeReportRepo(raise_on_by_id=True, raise_on_latest=True)
        sub = _make_subagent(report_repo=repo)

        sections = await sub.fetch_dynamic_context_sections("tenant-A")

        self.assertEqual(_section_texts(sections), [])


if __name__ == "__main__":
    unittest.main()
