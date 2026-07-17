from simpleclaw.subagent.runtime import (
    SubagentArtifact,
    SubagentRunRequest,
    SubagentRunResult,
    subagent_run_scope_key,
)


def test_subagent_run_request_derives_scope_and_dedupe_key():
    req = SubagentRunRequest(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        subagent_name="dream",
        objective="Audit memory ledger",
        run_mode="dream",
        owner_type="memory_ledger",
        owner_id="memledger_1",
        input_refs={"cursor": "0:40"},
        permission_profile=["read_memory", "write_artifact"],
    )

    assert req.scope_key == "subagent:tenant-1:main:tenant-1:dream:memory_ledger:memledger_1"
    assert req.effective_dedupe_key.endswith(":dream:0:40")
    assert req.allows("read_memory")
    assert not req.allows("notify_user")
    assert req.admitted().status == "admitted"
    assert req.status == "candidate"


def test_subagent_run_scope_key_handles_missing_owner():
    assert subagent_run_scope_key(
        tenant_key="tenant-1",
        session_key=None,
        subagent_name="skin_diary",
    ) == "subagent:tenant-1:__global__:skin_diary:__unowned__"


def test_subagent_artifact_status_transition_is_immutable():
    artifact = SubagentArtifact(
        run_id="subrun_1",
        artifact_type="memory_summary",
        content="建议补充一条长期记忆。",
        owner_type="dream_job",
        owner_id="dreamjob_1",
    )

    validated = artifact.with_status("validated")

    assert artifact.status == "draft"
    assert validated.status == "validated"
    assert validated.artifact_id == artifact.artifact_id


def test_subagent_run_result_factories():
    artifact = SubagentArtifact(
        run_id="subrun_1",
        artifact_type="memory_summary",
        content="memory update draft",
    )

    completed = SubagentRunResult.completed(
        "subrun_1",
        summary="produced artifact",
        artifacts=[artifact],
        read_refs={"memory_ledger_ids": ["memledger_1"]},
    )
    failed = SubagentRunResult.failed("subrun_2", "tool denied")

    assert completed.ok
    assert completed.status == "completed"
    assert completed.artifacts == [artifact]
    assert completed.read_refs["memory_ledger_ids"] == ["memledger_1"]
    assert not failed.ok
    assert failed.last_error == "tool denied"
