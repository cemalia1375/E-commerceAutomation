from __future__ import annotations

import json
from types import SimpleNamespace
import unittest

from Mojing.api.routes.journey import journey_event
from Mojing.journey.rules import record_journey_event


class _TenantStateRepo:
    def __init__(self, journey: dict | None = None) -> None:
        self.journey = journey or {"stage": "novice", "milestones": {}}
        self.saved: list[tuple[str, dict]] = []

    async def get_journey(self, tenant_key: str) -> dict:
        del tenant_key
        return {
            "stage": self.journey["stage"],
            "milestones": dict(self.journey["milestones"]),
        }

    async def save_journey(self, tenant_key: str, journey: dict) -> None:
        self.saved.append((tenant_key, journey))
        self.journey = {
            "stage": journey["stage"],
            "milestones": dict(journey["milestones"]),
        }


class _Sessions:
    def __init__(self) -> None:
        self.swaps: list[tuple[str, str]] = []

    async def swap_tenant_overlay(self, tenant_key: str, stage: str) -> int:
        self.swaps.append((tenant_key, stage))
        return 1


class _Request:
    def __init__(self, payload: dict, repo: _TenantStateRepo, sessions: _Sessions | None = None) -> None:
        self._payload = payload
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                container=SimpleNamespace(
                    tenant_state_repo=repo,
                    sessions=sessions,
                ),
            ),
        )

    async def json(self) -> dict:
        return dict(self._payload)


class JourneyRulesTest(unittest.IsolatedAsyncioTestCase):
    async def test_explore_entered_promotes_novice_to_explore(self) -> None:
        repo = _TenantStateRepo()

        stage_before, stage_after = await record_journey_event(
            repo,
            "298",
            "explore_entered",
        )

        self.assertEqual(stage_before, "novice")
        self.assertEqual(stage_after, "explore")
        self.assertEqual(repo.saved, [
            ("298", {
                "stage": "explore",
                "milestones": {"explore_entered": True},
            }),
        ])

    async def test_skin_diary_generated_promotes_novice_to_explore(self) -> None:
        repo = _TenantStateRepo()

        stage_before, stage_after = await record_journey_event(
            repo,
            "298",
            "skin_diary_generated",
        )

        self.assertEqual(stage_before, "novice")
        self.assertEqual(stage_after, "explore")
        self.assertEqual(repo.saved, [
            ("298", {
                "stage": "explore",
                "milestones": {"skin_diary_generated": True},
            }),
        ])

    async def test_explore_entered_is_idempotent_after_promotion(self) -> None:
        repo = _TenantStateRepo({
            "stage": "explore",
            "milestones": {"explore_entered": True},
        })

        stage_before, stage_after = await record_journey_event(
            repo,
            "298",
            "explore_entered",
        )

        self.assertEqual(stage_before, "explore")
        self.assertEqual(stage_after, "explore")
        self.assertEqual(repo.saved, [])

    async def test_journey_event_route_promotes_and_swaps_overlay(self) -> None:
        repo = _TenantStateRepo()
        sessions = _Sessions()

        response = await journey_event(_Request(
            {"tenant_key": "298", "event": "explore_entered"},
            repo,
            sessions,
        ))
        payload = json.loads(response.body)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["stage_before"], "novice")
        self.assertEqual(payload["stage_after"], "explore")
        self.assertTrue(payload["promoted"])
        self.assertEqual(sessions.swaps, [("298", "explore")])


if __name__ == "__main__":
    unittest.main()
