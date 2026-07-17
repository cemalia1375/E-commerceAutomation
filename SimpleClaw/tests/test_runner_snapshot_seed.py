from __future__ import annotations

from types import SimpleNamespace

import pytest

from script.runner import runner


class _DocRepo:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str, str]] = []

    async def set(self, tenant_key: str, name: str, content: str) -> None:
        self.writes.append((tenant_key, name, content))


@pytest.mark.asyncio
async def test_apply_seed_clones_snapshot_before_manual_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    async def fake_clone_to_tenant(db, **kwargs):
        calls.append({"db": db, **kwargs})

    monkeypatch.setattr("script.clone_tenant_snapshot.clone_to_tenant", fake_clone_to_tenant)

    db = object()
    docs = _DocRepo()
    container = SimpleNamespace(db=db, doc_repo=docs)

    await runner._apply_seed(
        container,
        tenant_key="test_dst",
        session_key="main:test_dst",
        scenario={
            "seed": {
                "from_snapshot": {
                    "tenant": "395",
                    "session": "main:session_395_source",
                    "msg_seq_cutoff": 58,
                    "snapshot_at": "2026-06-10 12:00:00",
                    "profile_limit": 4,
                    "diary_limit": 5,
                    "image_limit": 6,
                    "force": True,
                },
                "docs": {
                    "USER.md": "manual override",
                },
            }
        },
    )

    assert calls == [
        {
            "db": db,
            "src_tenant": "395",
            "src_session": "main:session_395_source",
            "dst_tenant": "test_dst",
            "dst_session": "main:test_dst",
            "msg_seq_cutoff": 58,
            "snapshot_at": "2026-06-10 12:00:00",
            "profile_limit": 4,
            "diary_limit": 5,
            "image_limit": 6,
            "force": True,
        }
    ]
    assert docs.writes == [("test_dst", "USER.md", "manual override")]


@pytest.mark.asyncio
async def test_seed_from_snapshot_rejects_same_source_and_destination_tenant() -> None:
    container = SimpleNamespace(db=object(), doc_repo=_DocRepo())

    with pytest.raises(ValueError, match="destination tenant must differ"):
        await runner._apply_seed(
            container,
            tenant_key="395",
            session_key="main:395",
            scenario={
                "seed": {
                    "from_snapshot": {
                        "tenant": "395",
                        "session": "main:session_395_source",
                        "msg_seq_cutoff": 58,
                    }
                }
            },
        )


@pytest.mark.asyncio
async def test_seed_from_snapshot_accepts_admin_style_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    async def fake_clone_to_tenant(db, **kwargs):
        del db
        calls.append(kwargs)

    monkeypatch.setattr("script.clone_tenant_snapshot.clone_to_tenant", fake_clone_to_tenant)

    await runner._apply_seed(
        SimpleNamespace(db=object(), doc_repo=_DocRepo()),
        tenant_key="test_alias",
        session_key="main:test_alias",
        scenario={
            "seed": {
                "from_snapshot": {
                    "tenant_id": "395",
                    "session_id": "main:session_395_source",
                    "cutoff": "58",
                    "force": "false",
                }
            }
        },
    )

    assert calls[0]["src_tenant"] == "395"
    assert calls[0]["src_session"] == "main:session_395_source"
    assert calls[0]["msg_seq_cutoff"] == 58
    assert calls[0]["force"] is False


@pytest.mark.asyncio
async def test_seed_from_snapshot_resolves_cutoff_from_snapshot_time(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    async def fake_resolve_cutoff(db, **kwargs):
        assert kwargs == {
            "src_tenant": "395",
            "src_session": "main:session_395_source",
            "snapshot_at": "2026-06-10 00:59:24",
            "inclusive": False,
        }
        return 57

    async def fake_clone_to_tenant(db, **kwargs):
        del db
        calls.append(kwargs)

    monkeypatch.setattr(runner, "_resolve_snapshot_cutoff", fake_resolve_cutoff)
    monkeypatch.setattr("script.clone_tenant_snapshot.clone_to_tenant", fake_clone_to_tenant)

    await runner._apply_seed(
        SimpleNamespace(db=object(), doc_repo=_DocRepo()),
        tenant_key="test_time",
        session_key="main:test_time",
        scenario={
            "seed": {
                "from_snapshot": {
                    "tenant": "395",
                    "session": "main:session_395_source",
                    "snapshot_at": "2026/6/10 0:59:24",
                }
            }
        },
    )

    assert calls[0]["msg_seq_cutoff"] == 57
    assert calls[0]["snapshot_at"] == "2026-06-10 00:59:24"
