"""Session ingress 协议 — 主会话输入收口与调度的通用定义。

这层不负责工具执行，也不负责具体业务成功与否。它只描述：

  - 谁可以请求某个 session 开启一轮 turn
  - 这些请求以什么形态进入队列
  - scheduler 最终如何把它们送入真正的 turn runner
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


SessionIngressMessageType = Literal["user_message", "system_activation"]
SessionIngressPriority = Literal["high", "low"]
SessionIngressTurnKind = Literal["user_turn", "system_turn"]
SessionIngressDeliveryPolicy = Literal["must_run", "best_effort"]
SessionIngressPreemptPolicy = Literal[
    "keep",
    "drop_if_session_busy",
    "drop_if_user_arrives",
    "drop_if_session_busy_or_user_arrives",
]
SessionIngressStatus = Literal[
    "queued",     #   已经入队，等待 scheduler 决定是否消费。
    "dispatching",#   scheduler 已经选中这条 ingress，正在把它送入真正的 turn executor。
    "delivered",  #   这条 ingress 已成功触发一轮 session turn；这里只表示“调度已完成”，
    "dropped",    #   这条 ingress 被明确放弃消费，属于预期内的调度决策，不是异常。
    "superseded", #   这条 ingress 被更新的同类 ingress 覆盖，旧输入不再需要执行。
    "expired",    #   这条 ingress 到达过期时间仍未被消费，因此直接失效。
    "failed",     #   调度或 turn dispatch 过程中发生异常，未能正常完成。
]
SessionIngressDispatchStatus = Literal["delivered", "dropped"]


def _now_ms() -> int:
    return int(time.time() * 1000)


def make_ingress_id() -> str:
    return uuid.uuid4().hex


@dataclass(slots=True)
class SessionIngressItem:
    """一条待进入 session turn 的输入对象。"""

    session_key: str
    message_type: SessionIngressMessageType
    payload_json: dict[str, Any]
    tenant_key: str | None = None
    priority: SessionIngressPriority = "high"
    turn_kind: SessionIngressTurnKind = "user_turn"
    delivery_policy: SessionIngressDeliveryPolicy = "must_run"
    preempt_policy: SessionIngressPreemptPolicy = "keep"
    source: str = "unknown"
    dedupe_key: str | None = None
    expires_at_ms: int | None = None
    status: SessionIngressStatus = "queued"
    summary: str | None = None
    error: str | None = None
    ingress_id: str = field(default_factory=make_ingress_id)
    created_at_ms: int = field(default_factory=_now_ms)
    updated_at_ms: int = field(default_factory=_now_ms)

    @classmethod
    def user_message(
        cls,
        *,
        session_key: str,
        tenant_key: str | None,
        content: str,
        source: str = "app",
        payload_json: dict[str, Any] | None = None,
    ) -> "SessionIngressItem":
        payload = {"content": content}
        if payload_json:
            payload.update(payload_json)
        return cls(
            session_key=session_key,
            tenant_key=tenant_key,
            message_type="user_message",
            priority="high",
            turn_kind="user_turn",
            delivery_policy="must_run",
            preempt_policy="keep",
            source=source,
            payload_json=payload,
        )

    @classmethod
    def system_activation(
        cls,
        *,
        session_key: str,
        tenant_key: str | None,
        payload_json: dict[str, Any],
        source: str,
        priority: SessionIngressPriority = "low",
        delivery_policy: SessionIngressDeliveryPolicy = "best_effort",
        preempt_policy: SessionIngressPreemptPolicy = "drop_if_session_busy_or_user_arrives",
        dedupe_key: str | None = None,
        expires_at_ms: int | None = None,
        summary: str | None = None,
    ) -> "SessionIngressItem":
        return cls(
            session_key=session_key,
            tenant_key=tenant_key,
            message_type="system_activation",
            priority=priority,
            turn_kind="system_turn",
            delivery_policy=delivery_policy,
            preempt_policy=preempt_policy,
            source=source,
            payload_json=dict(payload_json or {}),
            dedupe_key=dedupe_key,
            expires_at_ms=expires_at_ms,
            summary=summary,
        )

    def is_terminal(self) -> bool:
        return self.status in {
            "delivered",
            "dropped",
            "superseded",
            "expired",
            "failed",
        }

    def is_expired(self, now_ms: int | None = None) -> bool:
        if self.expires_at_ms is None:
            return False
        current = _now_ms() if now_ms is None else now_ms
        return current >= self.expires_at_ms

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SessionIngressDispatchResult:
    """Turn runner 对单条 ingress 的执行结果。"""

    status: SessionIngressDispatchStatus
    summary: str | None = None

    @classmethod
    def delivered(cls, summary: str | None = None) -> "SessionIngressDispatchResult":
        return cls(status="delivered", summary=summary)

    @classmethod
    def dropped(cls, summary: str | None = None) -> "SessionIngressDispatchResult":
        return cls(status="dropped", summary=summary)
