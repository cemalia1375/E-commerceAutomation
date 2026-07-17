from __future__ import annotations

from simpleclaw.dream import DreamCandidate, DreamSignal


def test_candidate_can_be_built_from_memory_signal() -> None:
    signal = DreamSignal(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        namespace="main",
        signal_type="memory_ledger_applied",
        reason="memory ledger may need review",
        subject_type="memory_ledger",
        subject_id="memledger-1",
        source_type="memory_compression",
        source_id="memledger-1",
        input_cursor="0:36",
        read_assets=["memory_ledger", "session_messages", "memory_entries"],
        write_assets=["dream_artifact"],
    )

    candidate = DreamCandidate.from_signal(signal, trigger="memory_threshold")

    assert candidate.tenant_key == "tenant-1"
    assert candidate.session_key == "main:tenant-1"
    assert candidate.namespace == "main"
    assert candidate.trigger == "memory_threshold"
    assert candidate.source_id == "memledger-1"
    assert candidate.input_cursor == "0:36"
    assert candidate.payload["signal"]["signal_type"] == "memory_ledger_applied"
    assert candidate.payload["read_assets"] == ["memory_ledger", "session_messages", "memory_entries"]
    assert signal.scope_key == "dream:tenant-1:main:tenant-1:main"
    assert signal.dedupe_key.endswith(":memory_ledger_applied:memledger-1")
    assert signal.merge_key == "dream:tenant-1:main:tenant-1:main:memory_ledger"


def test_signal_preserves_forbidden_assets() -> None:
    signal = DreamSignal(
        tenant_key="tenant-1",
        session_key="skin_diary:tenant-1",
        namespace="skin_diary",
        signal_type="skin_diary_generated",
        reason="review skin diary consistency with memory and USER.md",
        subject_type="skin_diary_result",
        subject_id="423",
        source_type="runtime_task",
        source_id="task-skin-diary-1",
        read_assets=["skin_diary_result", "USER.md", "main_memory", "skin_diary_memory"],
        write_assets=["dream_artifact"],
        forbidden_assets=["skin_diary_result"],
    )

    candidate = signal.to_candidate()

    assert candidate.trigger == "system_monitor"
    assert candidate.source_id == "task-skin-diary-1"
    assert candidate.payload["signal"]["subject_id"] == "423"
    assert candidate.payload["forbidden_assets"] == ["skin_diary_result"]
