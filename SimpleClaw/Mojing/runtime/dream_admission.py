"""Mojing-specific admission context for low-priority dream jobs."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from simpleclaw.dream import DreamAdmissionContext, DreamCandidate

if TYPE_CHECKING:
    from Mojing.api.session_ingress import MainSessionIngressCoordinator


class MojingDreamAdmissionContextBuilder:
    """Translate Mojing runtime ingress state into generic dream admission facts."""

    def __init__(
        self,
        *,
        ingress_getter: Callable[[], "MainSessionIngressCoordinator | None"],
    ) -> None:
        self._ingress_getter = ingress_getter

    async def build(self, candidate: DreamCandidate) -> DreamAdmissionContext:
        ingress = self._ingress_getter()
        if ingress is None:
            return DreamAdmissionContext(metadata={"ingress_available": False})

        pending = await ingress.store.list_pending(session_key=candidate.session_key, limit=100)
        user_pending = any(item.message_type == "user_message" for item in pending)
        activation_pending = any(item.message_type == "system_activation" for item in pending)
        return DreamAdmissionContext(
            session_busy=await ingress.scheduler.is_busy(candidate.session_key),
            user_ingress_pending=user_pending,
            higher_priority_activation_pending=activation_pending,
            metadata={
                "ingress_available": True,
                "pending_ingress_count": len(pending),
                "pending_user_ingress_count": sum(1 for item in pending if item.message_type == "user_message"),
                "pending_activation_count": sum(1 for item in pending if item.message_type == "system_activation"),
            },
        )
