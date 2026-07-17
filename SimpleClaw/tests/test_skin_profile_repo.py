import unittest
from typing import Any

from Mojing.storage.skin_profile_repo import SkinProfileRepository


class _ProbeSkinProfileRepository(SkinProfileRepository):
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def _fetch_one_profile(
        self,
        where_sql: str,
        params: tuple[Any, ...],
    ) -> dict[str, Any] | None:
        self.calls.append((where_sql, params))
        if "image_url = %s" in where_sql:
            return {"profile_id": 1, "message_id": "old-message"}
        return None


class SkinProfileRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_find_profile_since_does_not_fallback_when_message_id_is_available(self) -> None:
        repo = _ProbeSkinProfileRepository()

        profile = await repo.find_profile_since(
            tenant_key="tenant-1",
            since="2026-05-31 08:01:15",
            image_ref="https://example.test/reused-face.png",
            message_id="new-message",
        )

        self.assertIsNone(profile)
        self.assertEqual(len(repo.calls), 1)
        self.assertIn("message_id = %s", repo.calls[0][0])
        self.assertEqual(repo.calls[0][1], ("tenant-1", "2026-05-31 08:01:15", "new-message"))

    async def test_find_profile_since_can_fallback_to_image_ref_without_message_id(self) -> None:
        repo = _ProbeSkinProfileRepository()

        profile = await repo.find_profile_since(
            tenant_key="tenant-1",
            since="2026-05-31 08:01:15",
            image_ref="https://example.test/reused-face.png",
        )

        self.assertEqual(profile, {"profile_id": 1, "message_id": "old-message"})
