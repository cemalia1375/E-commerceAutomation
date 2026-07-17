from __future__ import annotations

from pathlib import Path


def test_running_scope_keys_only_counts_dream_jobs() -> None:
    source = Path("Mojing/storage/dream_repo.py").read_text(encoding="utf-8")

    assert "status IN ('admitted', 'running')" in source
    assert "JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.kind'))='dream_job'" in source
