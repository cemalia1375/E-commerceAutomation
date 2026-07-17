import pytest

from Mojing.agent.cold_path import (
    ColdPathHook,
    _action_already_submitted,
    _build_evidence,
    _build_payload,
    _copy_dependency_binding_to_evidence,
    _dedupe_key,
    _fill_user_template,
    _normalize_cancel_items,
    _normalize_obligation_items,
)
from Mojing.runtime.obligations import (
    ACTION_GENERATE_DEEP_REPORT,
    ACTION_GENERATE_SKIN_DIARY,
    DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
)
from simpleclaw.harness.hooks import TurnContext


class _UnusedLLM:
    pass


class _RecordingObligationRepo:
    def __init__(self) -> None:
        self.created: list[dict] = []

    async def find_pending_action(self, **kwargs):
        del kwargs
        return None

    async def create_pending(self, **kwargs):
        self.created.append(kwargs)
        return {"status": "pending", **kwargs}

    async def cancel_pending(self, **kwargs):
        del kwargs
        return 0


class _ActiveImageTaskRepo:
    def __init__(self, task: dict | None) -> None:
        self.task = task
        self.calls: list[dict] = []

    async def find_latest_active_task_for(self, **kwargs):
        self.calls.append(kwargs)
        return self.task


def test_fill_user_template_uses_turn_outputs_without_topic_state():
    text = _fill_user_template(
        template=(
            "media=<<<MEDIA_SIGNAL>>>\n"
            "user=<<<USER_MESSAGE>>>\n"
            "first=<<<FIRST_TOKEN_REPLY>>>\n"
            "main=<<<MAIN_ASSISTANT_REPLY>>>\n"
            "assistant=<<<ASSISTANT_REPLY>>>\n"
            "tools=<<<TOOL_FACTS>>>"
        ),
        user_message="图片分析好了也同步今天的护肤计划吧",
        first_token_reply="好呀",
        main_assistant_reply="等图片分析完成后，我会帮你同步今天的护肤计划。",
        assistant_reply="好呀\n等图片分析完成后，我会帮你同步今天的护肤计划。",
        media=[],
        current_topics={"legacy": "ignored"},
        current_mood={"label": "ignored"},
        tool_invocations=[{"tool_name": "generate_skin_diary", "status": "submitted"}],
    )

    assert "图片分析好了也同步今天的护肤计划吧" in text
    assert "等图片分析完成后" in text
    assert "无" in text
    assert "legacy" not in text
    assert "ignored" not in text
    assert "generate_skin_diary" in text


def test_normalize_obligation_items_keeps_only_supported_dependency():
    items = _normalize_obligation_items([
        {
            "action_type": ACTION_GENERATE_SKIN_DIARY,
            "dependency_type": DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
            "evidence": {"user_request": "分析完也同步护肤计划"},
        },
        {
            "action_type": ACTION_GENERATE_SKIN_DIARY,
            "dependency_type": "immediate",
        },
        {
            "action_type": ACTION_GENERATE_DEEP_REPORT,
            "dependency_type": DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
            "evidence": {"user_request": "分析完也帮我生成深度分析报告"},
        },
        {
            "action_type": "topic_reminder",
            "dependency_type": DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
        },
    ])

    assert len(items) == 2
    assert items[0]["action_type"] == ACTION_GENERATE_SKIN_DIARY
    assert items[0]["dependency_type"] == DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED
    assert items[1]["action_type"] == ACTION_GENERATE_DEEP_REPORT
    assert items[1]["dependency_type"] == DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED


def test_normalize_cancel_items_keeps_only_supported_action():
    assert _normalize_cancel_items([
        ACTION_GENERATE_SKIN_DIARY,
        ACTION_GENERATE_DEEP_REPORT,
        {"action_type": ACTION_GENERATE_SKIN_DIARY},
        {"action_type": ACTION_GENERATE_DEEP_REPORT},
        "topic_reminder",
    ]) == [
        ACTION_GENERATE_SKIN_DIARY,
        ACTION_GENERATE_DEEP_REPORT,
        ACTION_GENERATE_SKIN_DIARY,
        ACTION_GENERATE_DEEP_REPORT,
    ]


def test_build_payload_binds_current_image_analysis_invocation():
    ctx = TurnContext(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        user_message="看完这张图后生成今日肌肤日记",
        assistant_reply="我会等分析完成后生成。",
        media=["https://example.com/current.jpg"],
        tool_invocations=[
            {
                "tool_name": "analyze_image",
                "status": "submitted",
                "runtime_task_id": "image-task-current",
                "business_ref_type": "image_analysis_job",
                "business_ref_id": "job-current",
            }
        ],
    )
    item = {
        "action_type": ACTION_GENERATE_SKIN_DIARY,
        "dependency_type": DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
    }

    payload = _build_payload(ctx, item)
    evidence = _build_evidence(ctx, item)
    _copy_dependency_binding_to_evidence(evidence, payload)

    assert payload["dependency_ref_type"] == "runtime_task"
    assert payload["dependency_ref_id"] == "image-task-current"
    assert payload["dependency_business_ref_type"] == "image_analysis_job"
    assert payload["dependency_business_ref_id"] == "job-current"
    assert payload["dependency_binding_source"] == "tool_invocation"
    assert evidence["dependency_ref_id"] == "image-task-current"


def test_build_payload_requires_current_image_ref_when_media_has_no_task_fact():
    ctx = TurnContext(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        user_message="看完这张图后生成今日肌肤日记",
        assistant_reply="我会等分析完成后生成。",
        media=["https://example.com/current.jpg"],
    )

    payload = _build_payload(ctx, {
        "action_type": ACTION_GENERATE_SKIN_DIARY,
        "dependency_type": DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
    })

    assert payload["dependency_ref_required"] is True
    assert payload["dependency_media_count"] == 1
    assert "dependency_ref_id" not in payload


@pytest.mark.asyncio
async def test_cold_path_binds_active_image_task_for_cross_turn_skin_diary():
    obligation_repo = _RecordingObligationRepo()
    runtime_task_repo = _ActiveImageTaskRepo({
        "task_id": "image-task-active",
        "task_type": "image_analysis",
        "status": "wait_external",
        "business_ref_type": "image_analysis_job",
        "business_ref_id": "job-active",
        "payload": {"job_id": "job-active"},
    })
    hook = ColdPathHook(
        _UnusedLLM(),  # type: ignore[arg-type]
        obligation_repo,  # type: ignore[arg-type]
        runtime_task_repo=runtime_task_repo,
    )
    ctx = TurnContext(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        user_message="等等分析完成了以后，也帮我同步一下今天的肌肤日记吧。",
        assistant_reply="好，等图片分析完成后我会继续帮你生成今天的肌肤日记。",
    )

    created, cancelled = await hook._apply_obligation_result(ctx, {
        "obligations": [{
            "action_type": ACTION_GENERATE_SKIN_DIARY,
            "dependency_type": DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
        }],
    })

    assert created == 1
    assert cancelled == 0
    assert runtime_task_repo.calls == [{
        "tenant_key": "tenant-1",
        "session_key": "main:tenant-1",
        "task_type": "image_analysis",
    }]
    payload = obligation_repo.created[0]["payload"]
    evidence = obligation_repo.created[0]["evidence"]
    assert payload["dependency_ref_id"] == "image-task-active"
    assert payload["dependency_business_ref_id"] == "job-active"
    assert payload["dependency_binding_source"] == "runtime_task_active_lookup"
    assert "dependency_ref_required" not in payload
    assert evidence["dependency_ref_id"] == "image-task-active"


@pytest.mark.asyncio
async def test_cold_path_binds_active_image_task_for_cross_turn_deep_report():
    obligation_repo = _RecordingObligationRepo()
    runtime_task_repo = _ActiveImageTaskRepo({
        "task_id": "image-task-active",
        "task_type": "image_analysis",
        "status": "running",
    })
    hook = ColdPathHook(
        _UnusedLLM(),  # type: ignore[arg-type]
        obligation_repo,  # type: ignore[arg-type]
        runtime_task_repo=runtime_task_repo,
    )
    ctx = TurnContext(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        user_message="等等分析完成了以后，也帮我同步一下深度分析报告吧。",
        assistant_reply="好，等图片分析完成后我会继续帮你生成深度分析报告。",
    )

    created, cancelled = await hook._apply_obligation_result(ctx, {
        "obligations": [{
            "action_type": ACTION_GENERATE_DEEP_REPORT,
            "dependency_type": DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
        }],
    })

    assert created == 1
    assert cancelled == 0
    payload = obligation_repo.created[0]["payload"]
    assert payload["dependency_ref_id"] == "image-task-active"
    assert payload["dependency_binding_source"] == "runtime_task_active_lookup"
    assert payload["user_query"].startswith("等等分析完成了以后")


@pytest.mark.asyncio
async def test_cold_path_requires_dependency_ref_when_cross_turn_image_task_not_found():
    obligation_repo = _RecordingObligationRepo()
    runtime_task_repo = _ActiveImageTaskRepo(None)
    hook = ColdPathHook(
        _UnusedLLM(),  # type: ignore[arg-type]
        obligation_repo,  # type: ignore[arg-type]
        runtime_task_repo=runtime_task_repo,
    )
    ctx = TurnContext(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        user_message="等等分析完成了以后，也帮我同步一下深度分析报告吧。",
        assistant_reply="好，等图片分析完成后我会继续帮你生成深度分析报告。",
    )

    created, cancelled = await hook._apply_obligation_result(ctx, {
        "obligations": [{
            "action_type": ACTION_GENERATE_DEEP_REPORT,
            "dependency_type": DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
        }],
    })

    assert created == 1
    assert cancelled == 0
    payload = obligation_repo.created[0]["payload"]
    evidence = obligation_repo.created[0]["evidence"]
    assert payload["dependency_ref_required"] is True
    assert "dependency_ref_id" not in payload
    assert evidence["dependency_ref_required"] is True


def test_dedupe_key_includes_dependency_ref_id():
    common = {
        "tenant_key": "tenant-1",
        "session_key": "main:tenant-1",
        "action_type": ACTION_GENERATE_SKIN_DIARY,
        "dependency_type": DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
    }
    key_a = _dedupe_key(
        **common,
        evidence={
            "user_message": "看完这张图后生成今日肌肤日记",
            "assistant_reply": "我会等分析完成后生成。",
            "dependency_ref_id": "image-task-a",
        },
    )
    key_b = _dedupe_key(
        **common,
        evidence={
            "user_message": "看完这张图后生成今日肌肤日记",
            "assistant_reply": "我会等分析完成后生成。",
            "dependency_ref_id": "image-task-b",
        },
    )

    assert key_a != key_b


def test_action_already_submitted_from_tool_invocation():
    ctx = TurnContext(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        user_message="分析完给我生成今日肌肤日记",
        assistant_reply="我会帮你生成",
        tool_invocations=[
            {"tool_name": "generate_skin_diary", "status": "submitted"},
        ],
    )

    assert _action_already_submitted(ctx, ACTION_GENERATE_SKIN_DIARY)


def test_action_already_submitted_from_runtime_task():
    ctx = TurnContext(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        user_message="分析完给我生成今日肌肤日记",
        assistant_reply="我会帮你生成",
        runtime_tasks=[
            {"task_type": "skin_diary_generation", "status": "running"},
        ],
    )

    assert _action_already_submitted(ctx, ACTION_GENERATE_SKIN_DIARY)


def test_action_not_submitted_when_tool_was_blocked():
    ctx = TurnContext(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        user_message="分析完给我生成今日肌肤日记",
        assistant_reply="我会帮你生成",
        tool_invocations=[
            {"tool_name": "generate_skin_diary", "status": "blocked"},
        ],
    )

    assert not _action_already_submitted(ctx, ACTION_GENERATE_SKIN_DIARY)


def test_deep_report_action_already_submitted_from_tool_invocation():
    ctx = TurnContext(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        user_message="分析完给我生成深度分析报告",
        assistant_reply="等分析完成后我会帮你生成深度分析报告。",
        tool_invocations=[
            {"tool_name": "deep_report_chat", "status": "submitted"},
        ],
    )

    assert _action_already_submitted(ctx, ACTION_GENERATE_DEEP_REPORT)


def test_deep_report_action_already_submitted_from_runtime_task():
    ctx = TurnContext(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        user_message="分析完给我生成深度分析报告",
        assistant_reply="等分析完成后我会帮你生成深度分析报告。",
        runtime_tasks=[
            {"task_type": "deep_research", "status": "wait_external"},
        ],
    )

    assert _action_already_submitted(ctx, ACTION_GENERATE_DEEP_REPORT)


def test_deep_report_action_already_submitted_from_subagent_dispatch():
    ctx = TurnContext(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        user_message="分析完给我生成深度分析报告",
        assistant_reply="等分析完成后我会帮你生成深度分析报告。",
        runtime_tasks=[
            {
                "task_type": "subagent_dispatch",
                "status": "running",
                "input_json": {"session_key": "deep_report:tenant-1"},
            },
        ],
    )

    assert _action_already_submitted(ctx, ACTION_GENERATE_DEEP_REPORT)


def test_deep_report_action_not_submitted_when_tool_was_blocked():
    ctx = TurnContext(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        user_message="分析完给我生成深度分析报告",
        assistant_reply="等分析完成后我会帮你生成深度分析报告。",
        tool_invocations=[
            {"tool_name": "deep_report_chat", "status": "blocked"},
        ],
    )

    assert not _action_already_submitted(ctx, ACTION_GENERATE_DEEP_REPORT)
