from __future__ import annotations

from Mojing.dream.signals import (
    deep_report_completed_signal,
    skin_diary_generated_signal,
)


def test_skin_diary_generated_signal_declares_asset_boundary() -> None:
    signal = skin_diary_generated_signal(
        tenant_key="tenant-1",
        result_id="423",
        runtime_task_id="task-skin-1",
        business_date="2026-06-06",
    )

    assert signal.namespace == "skin_diary"
    assert signal.signal_type == "skin_diary_generated"
    assert signal.subject_type == "skin_diary_result"
    assert signal.subject_id == "423"
    assert signal.source_type == "runtime_task"
    assert signal.source_id == "task-skin-1"
    assert "skin_diary_result" in signal.read_assets
    assert "skin_diary_result" in signal.forbidden_assets
    assert "dream_artifact" in signal.write_assets


def test_deep_report_completed_signal_declares_asset_boundary() -> None:
    signal = deep_report_completed_signal(
        tenant_key="tenant-1",
        report_id="report-1",
        runtime_task_id="task-report-1",
    )

    assert signal.namespace == "deep_report"
    assert signal.signal_type == "deep_report_completed"
    assert signal.subject_type == "deep_report"
    assert signal.subject_id == "report-1"
    assert signal.source_type == "runtime_task"
    assert signal.source_id == "task-report-1"
    assert "deep_report" in signal.read_assets
    assert "deep_report" in signal.forbidden_assets
    assert "dream_artifact" in signal.write_assets
