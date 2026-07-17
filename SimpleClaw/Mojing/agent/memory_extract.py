"""从被压缩丢弃的消息中提取长期记忆。

以前这段逻辑混在 ColdPathHook 里、由后台 worker 调用——worker 还要反过来
摸主 Agent 的内存 Loop，架构扭曲。现在拆开：

  - 压缩本身（找边界 + 推 consolidated_from）由主 Agent 同步触发
    → 见 SessionStore.maybe_compress()
  - 被丢弃消息的 LLM 提取现在走 durable task：
    SessionStore / SubagentStore 负责入队，worker 消费后调用本模块执行提取
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from loguru import logger

from simpleclaw.core.messages import AssistantMessage, ToolCall, ToolResultMessage, UserMessage
from simpleclaw.llm.base import LLMProvider
from simpleclaw.llm.chunks import TextChunk
from simpleclaw.memory.ledger import MemorySnapshot
from simpleclaw.memory.ledger_store import MemoryLedgerStore
from simpleclaw.runtime.services import RuntimeServices
from simpleclaw.runtime.task_protocol import TaskEnvelope, TaskExecutionResult

from Mojing.agent.skin_guardrail import verify_skin_memory
from Mojing.agent.skin_trend import compute_trends, render_trend_facts
from Mojing.memory.business_snapshot import MojingMemoryBusinessSnapshotBuilder
from Mojing.runtime.streams import MojingTaskStream
from Mojing.storage.memory_repo import MySQLMemory

if TYPE_CHECKING:
    from Mojing.storage.database import Database
    from Mojing.storage.document_repo import DocumentRepository
    from Mojing.storage.runtime_task_repo import RuntimeTaskRepository


_COMPRESSION_PROMPT_PATH = Path(__file__).parent.parent / "workspace" / "compression_memory.md"

_MAX_MEMORY_SLOTS = 20
_GUARDRAIL_NOOP: dict[str, Any] = {"verdict": "accept", "rejected": [], "checked_lines": 0}
_MEMORY_MERGE_HINT_THRESHOLD = 15
_SKIN_TREND_WINDOW_DAYS = 30

# 趋势块注入门控（Option A）：cursor 记录"上次已反映进 skin 记忆的最新 profile_id"，
# 存 nb_tenant_profile_block_meta 的独立 block_name（与 skin_profile_sync 的
# "Learned Skin Profile" 不冲突）。只有出现更新画像才重新注块，避免静态画像被反复重写。
_SKIN_MEMORY_CURSOR_BLOCK = "memory_skin_cursor"
_SKIN_TREND_SKIP_NOTE = "本轮皮肤画像无新增，severity 时间线维持现状（不要仅为刷新趋势而新建/改写 skin 条目）"


async def _resolve_skin_injection(
    skin_profile_repo: Any, tenant_key: str, skin_trends: list
) -> tuple[bool, int | None]:
    """门控趋势块是否注入本轮 extract prompt。

    返回 (inject, latest_profile_id)：
    - 无 repo / 无趋势 / 无画像行 → (False, None)，块保持原状（无趋势时本就为空）。
    - 有画像但还没 cursor（首次）→ (True, latest_id)，注入并 bootstrap。
    - latest_id > cursor（出现更新画像）→ (True, latest_id)，注入。
    - latest_id <= cursor（画像静态）→ (False, latest_id)，跳过，避免重复重写同一组 severity。
    """
    if skin_profile_repo is None or not skin_trends:
        return (False, None)
    latest = await skin_profile_repo.get_latest(tenant_key)
    latest_id = (latest or {}).get("profile_id") if latest else None
    if latest_id is None:
        return (False, None)
    try:
        meta = await skin_profile_repo.get_block_meta(tenant_key, _SKIN_MEMORY_CURSOR_BLOCK)
    except Exception as exc:  # 读 cursor 失败：退化为注入（= 现状行为，安全）
        logger.warning("skin cursor read failed tenant={}: {}", tenant_key, exc)
        meta = None
    last_cursor = (meta or {}).get("last_profile_id")
    inject = last_cursor is None or int(latest_id) > int(last_cursor)
    return (inject, int(latest_id))


async def _advance_skin_cursor(
    skin_profile_repo: Any, tenant_key: str, latest_profile_id: int | None
) -> None:
    """把 cursor 推进到本轮已反映的最新 profile_id（仅在确实写了 skin 后调用）。"""
    if skin_profile_repo is None or latest_profile_id is None:
        return
    try:
        await skin_profile_repo.upsert_block_meta(
            tenant_key=tenant_key,
            block_name=_SKIN_MEMORY_CURSOR_BLOCK,
            last_writer="memory_extract",
            last_profile_id=latest_profile_id,
            content_hash="",
        )
    except Exception as exc:  # cursor 推进失败不影响主链（下次会重注，最多多写一次）
        logger.warning("skin cursor upsert failed tenant={}: {}", tenant_key, exc)


async def _compute_skin_trends(skin_profile_repo: Any, tenant_key: str) -> list:
    """读近 30 天 profiles → 算趋势；失败/无 repo 时返回 []（不阻断主链）。"""
    if skin_profile_repo is None or not tenant_key:
        return []
    try:
        end = datetime.utcnow()
        start = end - timedelta(days=_SKIN_TREND_WINDOW_DAYS)
        rows = await skin_profile_repo.list_profiles_in_range(tenant_key, start, end)
        return compute_trends(rows)
    except Exception as exc:  # 皮肤逻辑失败不得让对话记忆比"没有皮肤追踪"更糟
        logger.warning("skin trend compute failed tenant={}: {}", tenant_key, exc)
        return []


async def _build_skin_trend_block(skin_profile_repo: Any, tenant_key: str) -> str:
    """渲染喂入 extract prompt 的皮肤趋势事实块（字符串）。"""
    trends = await _compute_skin_trends(skin_profile_repo, tenant_key)
    return render_trend_facts(trends)


_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _now_note() -> str:
    """当前时间锚点（北京 UTC+8），让抽取时能把对话里的'今天/昨天'换算成绝对日期。"""
    now_cn = datetime.now(timezone(timedelta(hours=8)))
    return f"{now_cn.strftime('%Y-%m-%d %H:%M:%S')} {_WEEKDAY_CN[now_cn.weekday()]}"


@dataclass(slots=True)
class MemoryExtractionOutcome:
    memory_before: MemorySnapshot
    memory_actions: list[dict[str, Any]]
    memory_after: MemorySnapshot
    business_snapshot: dict[str, Any]
    guardrail: dict[str, Any] = field(default_factory=lambda: {"verdict": "accept", "rejected": [], "checked_lines": 0})
    skin_trends_injected: bool = False  # 本轮是否注入了皮肤趋势块（门控判定，便于观测/日志）


def load_compression_template() -> str:
    if _COMPRESSION_PROMPT_PATH.exists():
        return _COMPRESSION_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return ""


# ------------------------------------------------------------------
# 公共入口
# ------------------------------------------------------------------

ExtractCallback = Callable[..., Awaitable[None]]


def make_memory_extractor(
    *,
    llm: LLMProvider,
    db: "Database",
    source: str = "main",
    skin_profile_repo: Any = None,
) -> ExtractCallback:
    """返回 legacy direct extractor 回调 (tenant_key, dropped_messages) -> None。

    生产路径使用 make_memory_extract_submitter() 入 durable task queue；
    这个 direct extractor 仅保留给未接 runtime 的调用方或本地调试。
    所有异常都被吞并记录日志，不抛给调用方。
    """
    template = load_compression_template()

    async def extract(tenant_key: str, dropped_messages: list) -> None:
        if not template or not dropped_messages:
            return
        try:
            await _run_extraction(
                llm=llm,
                db=db,
                template=template,
                tenant_key=tenant_key,
                source=source,
                dropped_messages=dropped_messages,
                skin_profile_repo=skin_profile_repo,
            )
        except Exception as exc:
            logger.warning(
                "memory_extract failed tenant={} source={}: {}",
                tenant_key, source, exc,
            )

    return extract


def make_memory_extract_submitter(
    *,
    runtime: RuntimeServices,
    source: str = "main",
    memory_ledger_store: MemoryLedgerStore | None = None,
) -> ExtractCallback:
    """返回一个 async 回调 (tenant_key, dropped_messages) -> None。

    新实现不再直接执行提取，而是把 memory_extract durable task 入队。
    """

    async def submit(
        tenant_key: str,
        dropped_messages: list,
        *,
        session_key: str = "",
        ledger_id: str | None = None,
        last_consolidated_from: int | None = None,
        last_consolidated_to: int | None = None,
        message_seq_start: int | None = None,
        message_seq_end: int | None = None,
        trigger_type: str = "context_compression",
        tokens_before: int | None = None,
        tokens_after: int | None = None,
    ) -> None:
        if not dropped_messages:
            return
        serialized = _serialize_messages(dropped_messages)
        task = TaskEnvelope(
            task_type="memory_extract",
            payload={
                "tenant_key": tenant_key,
                "session_key": session_key,
                "source": source,
                "dropped_messages": serialized,
                "ledger_id": ledger_id or "",
                "last_consolidated_from": last_consolidated_from,
                "last_consolidated_to": last_consolidated_to,
                "message_seq_start": message_seq_start,
                "message_seq_end": message_seq_end,
                "trigger_type": trigger_type,
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "source_chunk_hash": _source_chunk_hash(serialized),
            },
            stream=MojingTaskStream.MEMORY_EXTRACT,
            tenant_key=tenant_key,
            session_key=session_key or None,
            scope_key=f"memory_extract:{tenant_key}:{source}",
            service_role="mojing:memory-extract",
        )
        try:
            await runtime.submit_task(task)
        except Exception as exc:
            if memory_ledger_store is not None and ledger_id:
                await memory_ledger_store.update_ledger(
                    ledger_id,
                    status="failed",
                    runtime_task_id=task.task_id,
                    trace_id=task.trace_id,
                    last_error=f"memory_extract enqueue failed: {exc}",
                    completed=True,
                )
            raise
        if memory_ledger_store is not None and ledger_id:
            await memory_ledger_store.update_ledger(
                ledger_id,
                runtime_task_id=task.task_id,
                trace_id=task.trace_id,
                metadata={"queue_submitted": True},
            )

    return submit


def make_memory_extract_executor(
    *,
    llm: LLMProvider,
    db: "Database",
    memory_ledger_store: MemoryLedgerStore | None = None,
    runtime_task_repo: "RuntimeTaskRepository | None" = None,
    document_repo: "DocumentRepository | None" = None,
    skin_profile_repo: Any = None,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """消费 memory_extract durable task。"""

    template = load_compression_template()

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        payload = task.payload
        tenant_key = payload.get("tenant_key") or task.tenant_key or ""
        session_key = str(payload.get("session_key") or task.session_key or "")
        source = str(payload.get("source") or "main")
        serialized = payload.get("dropped_messages") or []
        ledger_id = str(payload.get("ledger_id") or "").strip()
        if ledger_id and memory_ledger_store is not None:
            await memory_ledger_store.update_ledger(
                ledger_id,
                status="extracting",
                runtime_task_id=task.task_id,
                trace_id=task.trace_id,
            )
        if not template:
            await _mark_ledger_skipped(memory_ledger_store, ledger_id, "memory_extract template missing")
            return TaskExecutionResult.noop(summary="memory_extract template missing")
        if not tenant_key or not isinstance(serialized, list) or not serialized:
            await _mark_ledger_skipped(memory_ledger_store, ledger_id, "memory_extract payload empty")
            return TaskExecutionResult.noop(summary="memory_extract payload empty")
        try:
            dropped_messages = _deserialize_messages(serialized)
            if not dropped_messages:
                await _mark_ledger_skipped(memory_ledger_store, ledger_id, "memory_extract dropped messages empty")
                return TaskExecutionResult.noop(summary="memory_extract dropped messages empty")
            outcome = await _run_extraction(
                llm=llm,
                db=db,
                template=template,
                tenant_key=tenant_key,
                session_key=session_key,
                source=source,
                dropped_messages=dropped_messages,
                serialized_messages=serialized,
                business_snapshot_builder=MojingMemoryBusinessSnapshotBuilder(
                    runtime_task_repo=runtime_task_repo,
                    document_repo=document_repo,
                ),
                skin_profile_repo=skin_profile_repo,
            )
        except Exception as exc:
            if ledger_id and memory_ledger_store is not None:
                await memory_ledger_store.update_ledger(
                    ledger_id,
                    status="failed",
                    last_error=str(exc),
                    completed=True,
                )
            return TaskExecutionResult.failed(
                str(exc),
                summary=f"memory_extract failed: {exc}",
            )
        parse_failed = bool((outcome.business_snapshot or {}).get("parse_failed"))
        if parse_failed:
            error = "memory_extract parse failed: LLM output is not valid JSON"
            if ledger_id and memory_ledger_store is not None:
                await memory_ledger_store.update_ledger(
                    ledger_id,
                    status="failed",
                    memory_before=outcome.memory_before,
                    memory_actions=outcome.memory_actions,
                    memory_after=outcome.memory_after,
                    business_snapshot=outcome.business_snapshot,
                    metadata={"guardrail": outcome.guardrail},
                    last_error=error,
                    completed=True,
                )
            return TaskExecutionResult.failed(
                error,
                summary="memory_extract failed: parse failed",
                details={
                    "source": source,
                    "dropped_count": len(serialized),
                    "memory_actions": 0,
                    "ledger_id": ledger_id,
                },
            )
        if ledger_id and memory_ledger_store is not None:
            await memory_ledger_store.update_ledger(
                ledger_id,
                status="applied",
                memory_before=outcome.memory_before,
                memory_actions=outcome.memory_actions,
                memory_after=outcome.memory_after,
                business_snapshot=outcome.business_snapshot,
                metadata={"guardrail": outcome.guardrail},
                completed=True,
            )
        return TaskExecutionResult.succeeded(
            summary=f"memory_extract completed for source={source}",
            details={
                "source": source,
                "dropped_count": len(serialized),
                "memory_actions": len(outcome.memory_actions),
                "ledger_id": ledger_id,
            },
        )

    return execute


# ------------------------------------------------------------------
# 提取实现
# ------------------------------------------------------------------

def _summarize_guardrail(applied_actions: list[dict[str, Any]], skin_trends: list | None) -> dict[str, Any]:
    """汇总本次抽取的 skin 护栏判定，落点 nb_memory_ledgers.metadata['guardrail']。键名固定。"""
    skin_actions = [a for a in applied_actions if str(a.get("memory_type") or "").strip() == "skin"]
    checked_lines = len(skin_actions) if skin_trends else 0
    rejected: list[str] = []
    has_reject = has_backfill = False
    for a in skin_actions:
        if a.get("status") != "guardrail_rejected":
            continue
        outcome = a.get("guardrail_outcome") or "backfill"
        if outcome == "reject_line":
            has_reject = True
        else:
            has_backfill = True
        label = str(a.get("topic") or (f"topic_id={a.get('topic_id')}" if a.get("topic_id") else "skin"))
        viol = "；".join(a.get("violations") or []) or "与 profiles 趋势矛盾"
        verb = "丢弃" if outcome == "reject_line" else "回填"
        rejected.append(f"{label}：{viol}（已{verb}）")
    verdict = "reject_line" if has_reject else ("backfill" if has_backfill else "accept")
    return {"verdict": verdict, "rejected": rejected, "checked_lines": checked_lines}


async def _run_extraction(
    *,
    llm: LLMProvider,
    db: "Database",
    template: str,
    tenant_key: str,
    source: str,
    dropped_messages: list,
    session_key: str = "",
    serialized_messages: list[dict[str, Any]] | None = None,
    business_snapshot_builder: MojingMemoryBusinessSnapshotBuilder | None = None,
    skin_profile_repo: Any = None,
) -> MemoryExtractionOutcome:
    serialized_messages = serialized_messages or _serialize_messages(dropped_messages)
    business_snapshot = _business_snapshot_from_serialized(
        serialized_messages,
        tenant_key=tenant_key,
        session_key=session_key,
        source=source,
    )
    if business_snapshot_builder is not None:
        business_snapshot = await business_snapshot_builder.build(
            tenant_key=tenant_key,
            session_key=session_key,
            source=source,
            source_chunk=serialized_messages,
            base_snapshot=business_snapshot,
        )
    memory = MySQLMemory(db=db, tenant_key=tenant_key, source=source)
    current_memories = await memory.retrieve(top_k=_MAX_MEMORY_SLOTS)
    memory_before = _memory_snapshot(current_memories, source=source)

    chunk_text = _messages_to_text(dropped_messages)
    if not chunk_text.strip():
        return MemoryExtractionOutcome(
            memory_before=memory_before,
            memory_actions=[],
            memory_after=memory_before,
            business_snapshot=business_snapshot,
            guardrail=_GUARDRAIL_NOOP,
        )

    used = len(current_memories)
    slot_line = f"槽位已用 {used}/{_MAX_MEMORY_SLOTS}"
    if used >= _MEMORY_MERGE_HINT_THRESHOLD:
        slot_line += "（槽位紧张，优先考虑 merge 语义相近的话题，再考虑 create）"

    if current_memories:
        memory_index = "\n".join(
            f"{i + 1}. {m.key}｜{m.description}"
            for i, m in enumerate(current_memories)
        )
    else:
        memory_index = "（暂无记忆）"

    # 已有 skin 条目的完整正文：update 时需要在它的完整 severity_timeline 上演进，
    # 否则模型只看到一行 description，会把滚出当前 chunk 的历史日期行丢掉（数据丢失）。
    skin_existing = next(
        ((i, m) for i, m in enumerate(current_memories)
         if "severity_timeline" in (m.content or "")),
        None,
    )
    if skin_existing is not None:
        idx, m = skin_existing
        skin_current_block = (
            f"（对应上面 memory 索引第 {idx + 1} 条；update 时在此完整 severity_timeline 上演进）\n"
            + (m.content or "")
        )
    else:
        skin_current_block = "（暂无已有 skin 条目）"

    skin_trends = await _compute_skin_trends(skin_profile_repo, tenant_key)
    inject_skin, latest_profile_id = await _resolve_skin_injection(
        skin_profile_repo, tenant_key, skin_trends
    )
    if skin_trends and not inject_skin:
        # 画像静态：跳过趋势块注入，提示 LLM 本轮无需刷新 severity 时间线
        skin_trend_block = _SKIN_TREND_SKIP_NOTE
    else:
        skin_trend_block = render_trend_facts(skin_trends)

    prompt = (
        template
        .replace("<<<MSG_COUNT>>>", str(len(dropped_messages)))
        .replace("<<<CHUNK_TEXT>>>", chunk_text)
        .replace("<<<SLOT_LINE>>>", slot_line)
        .replace("<<<MEMORY_INDEX>>>", memory_index)
        .replace("<<<SKIN_TREND_FACTS>>>", skin_trend_block)
        .replace("<<<SKIN_CURRENT>>>", skin_current_block)
        .replace("<<<NOW>>>", _now_note())
    )

    raw = await _llm_complete(llm, prompt, max_tokens=4096)
    if not raw or raw == "{}":
        return MemoryExtractionOutcome(
            memory_before=memory_before,
            memory_actions=[],
            memory_after=memory_before,
            business_snapshot=business_snapshot,
            guardrail=_GUARDRAIL_NOOP,
            skin_trends_injected=inject_skin,
        )

    data = _parse_json_safe(raw)
    if data is None:
        logger.warning("memory_extract JSON parse failed raw={}", raw[:200])
        return MemoryExtractionOutcome(
            memory_before=memory_before,
            memory_actions=[],
            memory_after=memory_before,
            business_snapshot={**business_snapshot, "parse_failed": True, "raw_excerpt": raw[:500]},
            guardrail=_GUARDRAIL_NOOP,
            skin_trends_injected=inject_skin,
        )

    actions = (data.get("memory_actions") or [])[:4]
    applied_actions: list[dict[str, Any]] = []
    for action_obj in actions:
        applied_actions.append(
            await _execute_memory_action(action_obj, current_memories, memory, skin_trends=skin_trends)
        )
    memory_after = _memory_snapshot(await memory.retrieve(top_k=_MAX_MEMORY_SLOTS), source=source)

    # 本轮注了趋势块且确实写入了 skin，说明新画像已反映 → 推进 cursor，后续静态压缩不再重注
    if inject_skin and any(
        str(a.get("memory_type") or "").strip() == "skin" for a in applied_actions
    ):
        await _advance_skin_cursor(skin_profile_repo, tenant_key, latest_profile_id)

    logger.info(
        "memory_extract done tenant={} source={} dropped={} actions={}",
        tenant_key, source, len(dropped_messages), len(applied_actions),
    )
    return MemoryExtractionOutcome(
        memory_before=memory_before,
        memory_actions=applied_actions,
        memory_after=memory_after,
        business_snapshot=business_snapshot,
        guardrail=_summarize_guardrail(applied_actions, skin_trends),
        skin_trends_injected=inject_skin,
    )


async def _execute_memory_action(
    action_obj: dict[str, Any],
    current_memories: list,
    memory: MySQLMemory,
    *,
    skin_trends: list | None = None,
) -> dict[str, Any]:
    """执行一条 memory_action（create / append / update / merge）。"""
    record = dict(action_obj)
    action = action_obj.get("action", "")
    memory_type = str(action_obj.get("memory_type") or "chitchat").strip() or "chitchat"
    guardrail_rejected = False
    if memory_type == "skin" and skin_trends:
        guard = verify_skin_memory(
            description=str(action_obj.get("description") or action_obj.get("description_update") or ""),
            content=str(action_obj.get("content") or action_obj.get("new_fact") or ""),
            trends=skin_trends,
        )
        if not guard.ok:
            guardrail_rejected = True
            record["status"] = "guardrail_rejected"
            record["violations"] = guard.violations
            record["guardrail_outcome"] = "reject_line" if action == "append" else "backfill"
            action_obj = dict(action_obj)
            action_obj["description"] = guard.skeleton_description
            action_obj["content"] = guard.skeleton_timeline
            logger.warning("skin memory guardrail rejected: {}", guard.violations)
    try:
        if action == "create":
            await memory.store(
                key=action_obj["topic"],
                content=action_obj.get("content", ""),
                description=action_obj.get("description", ""),
                memory_type=memory_type,
            )
        elif action == "append":
            if guardrail_rejected:
                pass  # skin append 含矛盾数字，丢弃不写（skin 应走 update，见 compression_memory.md）
            else:
                idx = int(action_obj.get("topic_id", 0)) - 1
                if 0 <= idx < len(current_memories):
                    existing = current_memories[idx]
                    new_fact = action_obj.get("new_fact", "")
                    new_content = (
                        f"{existing.content}\n\n{new_fact}"
                        if existing.content else new_fact
                    )
                    new_desc = action_obj.get("description_update") or existing.description
                    await memory.store(key=existing.key, content=new_content, description=new_desc)
        elif action == "update":
            idx = int(action_obj.get("topic_id", 0)) - 1
            if 0 <= idx < len(current_memories):
                topic = current_memories[idx].key
                await memory.store(
                    key=topic,
                    content=action_obj.get("content", ""),
                    description=action_obj.get("description") or current_memories[idx].description,
                    memory_type=memory_type,
                )
        elif action == "merge":
            from_idx = int(action_obj.get("from_topic_id", 0)) - 1
            into_idx = int(action_obj.get("into_topic_id", 0)) - 1
            if 0 <= from_idx < len(current_memories) and 0 <= into_idx < len(current_memories):
                from_mem = current_memories[from_idx]
                into_mem = current_memories[into_idx]
                merged_content = (
                    f"{into_mem.content}\n\n{from_mem.content}"
                    if into_mem.content else from_mem.content
                )
                await memory.store(
                    key=into_mem.key,
                    content=merged_content,
                    description=into_mem.description,
                )
                await memory.delete(from_mem.key)
                logger.debug("memory_extract merge: '{}' → '{}' 完成", from_mem.key, into_mem.key)
        if record.get("status") != "guardrail_rejected":
            record["status"] = "applied"
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = str(exc)
        logger.warning("memory_extract action={} 失败：{}", action, exc)
    return record


# ------------------------------------------------------------------
# 辅助工具
# ------------------------------------------------------------------

async def _mark_ledger_skipped(
    memory_ledger_store: MemoryLedgerStore | None,
    ledger_id: str,
    reason: str,
) -> None:
    if memory_ledger_store is None or not ledger_id:
        return
    await memory_ledger_store.update_ledger(
        ledger_id,
        status="skipped",
        last_error=reason,
        completed=True,
    )


def _memory_snapshot(items: list, *, source: str) -> MemorySnapshot:
    return MemorySnapshot(
        items=[
            {
                "key": str(getattr(item, "key", "") or ""),
                "description": str(getattr(item, "description", "") or ""),
                "content": str(getattr(item, "content", "") or ""),
            }
            for item in items
        ],
        metadata={"source": source, "count": len(items)},
    )


def _business_snapshot_from_serialized(
    messages: list[dict[str, Any]],
    *,
    tenant_key: str,
    session_key: str,
    source: str,
) -> dict[str, Any]:
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    roles: dict[str, int] = {}
    for index, msg in enumerate(messages):
        role = str(msg.get("role") or "").strip()
        roles[role] = roles.get(role, 0) + 1
        if role == "assistant":
            for call in msg.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                tool_calls.append({
                    "message_index": index,
                    "tool_call_id": str(call.get("id") or ""),
                    "tool_name": str(call.get("name") or ""),
                    "arguments": call.get("arguments") if isinstance(call.get("arguments"), dict) else {},
                })
        elif role == "tool":
            tool_results.append({
                "message_index": index,
                "tool_call_id": str(msg.get("call_id") or ""),
                "result_excerpt": str(msg.get("content") or "")[:1000],
            })
    return {
        "tenant_key": tenant_key,
        "session_key": session_key,
        "source": source,
        "message_count": len(messages),
        "roles": roles,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
    }


def _source_chunk_hash(messages: list[dict[str, Any]]) -> str:
    raw = json.dumps(messages, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _llm_complete(llm: LLMProvider, prompt: str, max_tokens: int) -> str:
    parts: list[str] = []
    async for chunk in llm.stream_with_retry(
        [{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.0,
    ):
        if isinstance(chunk, TextChunk):
            parts.append(chunk.token)
    return "".join(parts).strip()


def _parse_json_safe(raw: str) -> dict | None:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end])
        except json.JSONDecodeError:
            return None
    return None


def _messages_to_text(messages: list) -> str:
    """把内部 Message 对象拼成可读 chunk 文本，喂给 LLM。"""
    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, UserMessage):
            lines.append(f"用户：{msg.content}")
        elif isinstance(msg, AssistantMessage):
            if msg.content:
                lines.append(f"魔镜：{msg.content}")
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    lines.append(f"[工具调用 {tc.name}]")
        elif isinstance(msg, ToolResultMessage):
            text = msg.content if isinstance(msg.content, str) else str(msg.content)
            lines.append(f"[工具结果] {text[:200]}")
    return "\n".join(lines)


def _serialize_messages(messages: list) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, UserMessage):
            result.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AssistantMessage):
            result.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                    }
                    for tc in msg.tool_calls
                ],
            })
        elif isinstance(msg, ToolResultMessage):
            result.append({
                "role": "tool",
                "call_id": msg.call_id,
                "content": msg.content,
            })
    return result


def _deserialize_messages(items: list[dict[str, Any]]) -> list:
    result: list = []
    for item in items:
        role = item.get("role")
        if role == "user":
            result.append(UserMessage(content=str(item.get("content") or "")))
        elif role == "assistant":
            tool_calls = [
                ToolCall(
                    id=str(tc.get("id") or ""),
                    name=str(tc.get("name") or ""),
                    arguments=tc.get("arguments") or {},
                )
                for tc in (item.get("tool_calls") or [])
                if isinstance(tc, dict)
            ]
            result.append(AssistantMessage(
                content=str(item.get("content") or ""),
                tool_calls=tool_calls,
            ))
        elif role == "tool":
            result.append(ToolResultMessage(
                call_id=str(item.get("call_id") or ""),
                content=str(item.get("content") or ""),
            ))
    return result
