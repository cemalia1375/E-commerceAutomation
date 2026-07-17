"""Tests for generate_skin_diary real generation/write path."""

from __future__ import annotations

import json
import sys
import types
import unittest
from datetime import datetime
from typing import Any

sys.modules.setdefault("loguru", types.SimpleNamespace(logger=types.SimpleNamespace(
    info=lambda *_, **__: None,
    debug=lambda *_, **__: None,
    warning=lambda *_, **__: None,
    error=lambda *_, **__: None,
)))
sys.modules.setdefault("json_repair", types.SimpleNamespace(loads=json.loads))

from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.llm.chunks import TextChunk
from Mojing.runtime.streams import MojingTaskStream
from Mojing.runtime.task_types import MojingTaskType
from Mojing.tools.generate_skin_diary import GenerateSkinDiaryTool


class _FakeLLM:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.messages: list[dict[str, Any]] = []

    async def stream_with_retry(self, messages, tools=None, **kwargs):
        del tools, kwargs
        self.messages = messages
        yield TextChunk(json.dumps(self.payload, ensure_ascii=False))


class _FakeDocumentRepo:
    def __init__(self, content: str | None = None) -> None:
        self.content = content or "## Learned Skin Profile\n\n- 肤质：混合偏油"

    async def get(self, tenant_key: str, doc_name: str) -> str | None:
        del tenant_key, doc_name
        return self.content


class _FakeSkinProfileRepo:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    async def get_latest(self, tenant_key: str) -> dict[str, Any] | None:
        del tenant_key
        return self.row

    @staticmethod
    def parse_json_field(raw: Any) -> Any:
        if raw is None or isinstance(raw, (list, dict)):
            return raw
        return json.loads(raw)


class _FakeResultRepo:
    def __init__(self) -> None:
        self.created: dict[str, Any] | None = None

    async def get_latest(self, tenant_key: str) -> dict[str, Any] | None:
        del tenant_key
        return None

    async def create_result(self, **kwargs: Any) -> int:
        self.created = kwargs
        return 42


class _FakeWeatherService:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    async def query(self, *, location: str, focus: str = "general", time_scope: str = "today") -> dict[str, Any]:
        self.calls.append({"location": location, "focus": focus, "time_scope": time_scope})
        return dict(self.payload)


def _profile_row() -> dict[str, Any]:
    return {
        "profile_id": 1,
        "tenant_key": "tenant-1",
        "image_url": "",
        "skin_attribute_json": {
            "stage": {"name": "轻熟肌"},
            "toneType": {"name": "自然肤色"},
            "oilType": {"name": "混合偏油"},
        },
        "overall_state": "鼻部黑头明显，脸颊轻微泛红",
        "advantages_json": ["肤色均匀"],
        "signals_json": [
            {
                "code": "blackheads",
                "name": "黑头",
                "regions": ["鼻子"],
                "severity": "重度",
                "careSuggestions": ["温和清洁", "减少摩擦"],
            }
        ],
        "created_at": datetime(2026, 5, 1, 9, 30),
    }


def _current_signal_profile_row() -> dict[str, Any]:
    row = _profile_row()
    row["signals_json"] = [
        {
            "code": "closed_comedones",
            "name": "闭口",
            "regions": ["额头"],
            "severity": "轻度",
            "careSuggestions": ["温和代谢角质", "减少厚重叠加"],
        },
        {
            "code": "visible_pores",
            "name": "毛孔粗大",
            "regions": ["鼻子"],
            "severity": "重度",
            "careSuggestions": ["控油", "收缩毛孔"],
        },
    ]
    return row


class GenerateSkinDiaryToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_task_queues_durable_generation(self) -> None:
        tool = GenerateSkinDiaryTool(
            llm=_FakeLLM({}),
            document_repo=_FakeDocumentRepo(),  # type: ignore[arg-type]
            skin_profile_repo=_FakeSkinProfileRepo(_profile_row()),  # type: ignore[arg-type]
            result_repo=_FakeResultRepo(),  # type: ignore[arg-type]
        )
        tool.set_context(
            tenant_key="tenant-1",
            session_key="skin_diary:tenant-1",
            query="就按下巴闭口重新生成一版",
        )

        task = await tool.prepare_task(
            confirmed_focus="下巴闭口",
            source="mixed",
            evidence="用户上传图片后确认下巴闭口",
        )

        self.assertIsInstance(task, TaskEnvelope)
        assert isinstance(task, TaskEnvelope)
        self.assertEqual(task.task_type, MojingTaskType.SKIN_DIARY_GENERATION)
        self.assertEqual(task.stream, MojingTaskStream.SKIN_DIARY)
        self.assertEqual(task.session_key, "skin_diary:tenant-1")
        self.assertEqual(task.payload["generation_input"]["confirmed_focus"], "下巴闭口")

        result = tool.durable_result(task, "queue-1")
        payload = json.loads(result.content)
        self.assertEqual(payload["action"], "submitted")
        self.assertEqual(payload["runtime_task_status"], "queued")
        self.assertEqual(payload["estimated_seconds"], 30)

    async def test_generates_and_persists_skin_diary_result(self) -> None:
        result_repo = _FakeResultRepo()
        progress_events: list[tuple[str, int, str]] = []
        tool = GenerateSkinDiaryTool(
            llm=_FakeLLM({
                "state": "stable",
                "summary": "鼻部黑头需要温和清洁，整体状态可控",
                "chips": [{
                    "label": "黑头",
                    "analysis": "鼻部黑头更明显，和油脂堆积有关。",
                    "suggestion": "晚间温和清洁，减少摩擦。",
                }],
                "morning_steps": [{
                    "order": 1,
                    "title": "轻柔洁面",
                    "usage": "用温水湿脸，鼻部轻轻打圈。",
                    "effect": "减少油脂堆积。",
                    "focus_area": "黑头",
                }],
                "evening_steps": [],
            }),
            document_repo=_FakeDocumentRepo(),  # type: ignore[arg-type]
            skin_profile_repo=_FakeSkinProfileRepo(_profile_row()),  # type: ignore[arg-type]
            result_repo=result_repo,  # type: ignore[arg-type]
        )
        tool.set_context(
            tenant_key="tenant-1",
            session_key="skin_diary:tenant-1",
            query=(
                "[系统通知] 业务日期=2026-05-01，日记时段=morning，"
                "生成原因=auto_morning，北京时间触发时间=2026-05-01 09:45:00。"
            ),
        )

        async def record_progress(stage_code: str, progress_percent: int, current_title: str) -> None:
            progress_events.append((stage_code, progress_percent, current_title))

        payload = await tool.generate(progress_callback=record_progress)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "generated")
        self.assertEqual(payload["result_id"], 42)
        self.assertEqual(
            progress_events,
            [
                ("diary_analysis", 25, "当日状态分析"),
                ("focus_summary", 45, "关注点整理"),
                ("routine_generation", 70, "护肤路径生成"),
                ("content_finalize", 90, "日记内容整理"),
            ],
        )
        self.assertIsNotNone(result_repo.created)
        assert result_repo.created is not None
        self.assertEqual(result_repo.created["tenant_key"], "tenant-1")
        self.assertEqual(result_repo.created["state"], "stable")
        self.assertEqual(result_repo.created["chips"][0]["label"], "黑头")
        self.assertEqual(result_repo.created["chips"][0]["isNew"], True)
        self.assertEqual(result_repo.created["chips"][0]["severity"], "重度")
        self.assertEqual(result_repo.created["create_time"], datetime(2026, 5, 1, 9, 45))

    async def test_current_signal_payload_shape_becomes_prompt_candidates(self) -> None:
        result_repo = _FakeResultRepo()
        llm = _FakeLLM({
            "state": "stable",
            "summary": "今日发现额头闭口和鼻部毛孔需要继续温和护理",
            "chips": [],
            "morning_steps": [],
            "evening_steps": [],
        })
        tool = GenerateSkinDiaryTool(
            llm=llm,
            document_repo=_FakeDocumentRepo(),  # type: ignore[arg-type]
            skin_profile_repo=_FakeSkinProfileRepo(_current_signal_profile_row()),  # type: ignore[arg-type]
            result_repo=result_repo,  # type: ignore[arg-type]
        )
        tool.set_context(tenant_key="tenant-1", session_key="skin_diary:tenant-1")

        result = await tool.execute()

        self.assertTrue(result.ok)
        prompt_text = llm.messages[1]["content"][0]["text"]
        self.assertIn("闭口｜严重程度：轻度｜部位：额头｜护理建议：温和代谢角质、减少厚重叠加", prompt_text)
        self.assertIn("毛孔粗大｜严重程度：重度｜部位：鼻子｜护理建议：控油、收缩毛孔", prompt_text)
        self.assertIsNotNone(result_repo.created)
        assert result_repo.created is not None
        raw_targets = result_repo.created["raw_output"]["issue_targets"]
        self.assertEqual([item["label"] for item in raw_targets], ["闭口", "毛孔粗大"])
        self.assertEqual(raw_targets[0]["regions"], ["额头"])
        self.assertEqual(raw_targets[0]["care_suggestions"], ["温和代谢角质", "减少厚重叠加"])

    async def test_missing_profile_returns_user_visible_failure(self) -> None:
        result_repo = _FakeResultRepo()
        tool = GenerateSkinDiaryTool(
            llm=_FakeLLM({}),
            document_repo=_FakeDocumentRepo(),  # type: ignore[arg-type]
            skin_profile_repo=_FakeSkinProfileRepo(None),  # type: ignore[arg-type]
            result_repo=result_repo,  # type: ignore[arg-type]
        )
        tool.set_context(tenant_key="tenant-1", session_key="skin_diary:tenant-1")

        result = await tool.execute()
        payload = json.loads(result.content)

        self.assertFalse(result.ok)
        self.assertEqual(payload["status"], "missing_skin_profile")
        self.assertIsNone(result_repo.created)

    async def test_maps_prompt_legacy_status_to_internal_state(self) -> None:
        result_repo = _FakeResultRepo()
        tool = GenerateSkinDiaryTool(
            llm=_FakeLLM({
                "status": "new_mild",
                "summary": "今日发现鼻部黑头轻微明显，整体仍然可控",
                "chips": [],
                "morning_steps": [],
                "evening_steps": [],
            }),
            document_repo=_FakeDocumentRepo(),  # type: ignore[arg-type]
            skin_profile_repo=_FakeSkinProfileRepo(_profile_row()),  # type: ignore[arg-type]
            result_repo=result_repo,  # type: ignore[arg-type]
        )
        tool.set_context(tenant_key="tenant-1", session_key="skin_diary:tenant-1")

        result = await tool.execute()

        self.assertTrue(result.ok)
        self.assertIsNotNone(result_repo.created)
        assert result_repo.created is not None
        self.assertEqual(result_repo.created["state"], "fluctuating")

    async def test_user_skin_focus_context_is_prompted_without_mutating_candidates(self) -> None:
        result_repo = _FakeResultRepo()
        llm = _FakeLLM({
            "state": "stable",
            "summary": "新版日记先围绕下巴闭口承接，鼻部黑头本轮不纳入。",
            "chips": [{
                "label": "下巴闭口",
                "analysis": "这是用户确认要重点观察的关注。",
                "suggestion": "晚间减少摩擦，观察是否刺痛。",
            }],
            "morning_steps": [],
            "evening_steps": [],
        })
        user_md = """## skin

### current_concerns

- 下巴闭口｜来源：用户主诉关注｜状态：已确认｜不作为图片检测结论

### excluded_concerns

- 黑头｜用户确认暂时不纳入新版肌肤日记

## Learned Skin Profile

- 肤质：混合偏油
"""
        tool = GenerateSkinDiaryTool(
            llm=llm,
            document_repo=_FakeDocumentRepo(user_md),  # type: ignore[arg-type]
            skin_profile_repo=_FakeSkinProfileRepo(_profile_row()),  # type: ignore[arg-type]
            result_repo=result_repo,  # type: ignore[arg-type]
        )
        tool.set_context(tenant_key="tenant-1", session_key="skin_diary:tenant-1")

        result = await tool.execute(
            confirmed_focus="下巴闭口",
            declined_focus="黑头",
            source="mixed",
            evidence="用户确认下巴闭口，本轮暂不看黑头",
        )

        self.assertTrue(result.ok)
        self.assertIsNotNone(result_repo.created)
        assert result_repo.created is not None
        labels = [item["label"] for item in result_repo.created["chips"]]
        self.assertEqual(labels, ["下巴闭口"])
        raw_targets = result_repo.created["raw_output"]["issue_targets"]
        self.assertEqual([item["label"] for item in raw_targets], ["黑头"])
        self.assertEqual(raw_targets[0]["severity"], "重度")
        self.assertEqual(result_repo.created["raw_output"]["generation_context"]["confirmed_focus"], "下巴闭口")
        self.assertNotIn("focus_overrides", result_repo.created["raw_output"]["generation_context"])
        prompt_text = llm.messages[1]["content"][0]["text"]
        self.assertIn("用户画像 USER.md", prompt_text)
        self.assertIn("生成上下文", prompt_text)
        self.assertIn("下巴闭口", prompt_text)
        self.assertIn("黑头", prompt_text)
        self.assertIn("严重程度：重度", prompt_text)

    async def test_city_in_user_profile_adds_weather_reference_to_generation_prompt(self) -> None:
        result_repo = _FakeResultRepo()
        llm = _FakeLLM({
            "state": "stable",
            "summary": "鼻部黑头需要温和清洁，整体状态可控",
            "chips": [],
            "morning_steps": [],
            "evening_steps": [],
        })
        weather_service = _FakeWeatherService({
            "ok": True,
            "location_query": "广州",
            "time_scope": "today",
            "user_visible_summary": "广东省广州市当前雷阵雨，湿度93%，紫外线指数强。",
        })
        user_md = """# 用户画像

当前城市：广州

## Learned Skin Profile

- 肤质：混合偏油
"""
        tool = GenerateSkinDiaryTool(
            llm=llm,
            document_repo=_FakeDocumentRepo(user_md),  # type: ignore[arg-type]
            skin_profile_repo=_FakeSkinProfileRepo(_profile_row()),  # type: ignore[arg-type]
            result_repo=result_repo,  # type: ignore[arg-type]
            weather_service=weather_service,  # type: ignore[arg-type]
        )
        tool.set_context(tenant_key="tenant-1", session_key="skin_diary:tenant-1")

        result = await tool.execute()

        self.assertTrue(result.ok)
        self.assertEqual(weather_service.calls, [{"location": "广州", "focus": "skincare", "time_scope": "today"}])
        self.assertIsNotNone(result_repo.created)
        assert result_repo.created is not None
        prompt_text = llm.messages[1]["content"][0]["text"]
        self.assertIn("## 今日天气参考", prompt_text)
        self.assertIn("广东省广州市当前雷阵雨", prompt_text)
        self.assertEqual(
            result_repo.created["raw_output"]["weather_reference"]["summary"],
            "广东省广州市当前雷阵雨，湿度93%，紫外线指数强。",
        )

    async def test_no_city_skips_weather_lookup(self) -> None:
        result_repo = _FakeResultRepo()
        llm = _FakeLLM({
            "state": "stable",
            "summary": "鼻部黑头需要温和清洁，整体状态可控",
            "chips": [],
            "morning_steps": [],
            "evening_steps": [],
        })
        weather_service = _FakeWeatherService({
            "ok": True,
            "location_query": "广州",
            "time_scope": "today",
            "user_visible_summary": "不应出现",
        })
        tool = GenerateSkinDiaryTool(
            llm=llm,
            document_repo=_FakeDocumentRepo("## Learned Skin Profile\n\n- 肤质：混合偏油"),  # type: ignore[arg-type]
            skin_profile_repo=_FakeSkinProfileRepo(_profile_row()),  # type: ignore[arg-type]
            result_repo=result_repo,  # type: ignore[arg-type]
            weather_service=weather_service,  # type: ignore[arg-type]
        )
        tool.set_context(tenant_key="tenant-1", session_key="skin_diary:tenant-1")

        result = await tool.execute()

        self.assertTrue(result.ok)
        self.assertEqual(weather_service.calls, [])
        prompt_text = llm.messages[1]["content"][0]["text"]
        self.assertIn("USER.md 未提供明确城市", prompt_text)


if __name__ == "__main__":
    unittest.main()
