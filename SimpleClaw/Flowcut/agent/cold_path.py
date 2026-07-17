"""ColdPathHook — 每轮对话结束后在后台执行的指令跟随（冷路径）。

由 topic_tracking stream 的 TaskWorker 触发，仅做一件事：

指令跟随（每轮必跑，轻量）
  cold_path.md → LLM（max_tokens=300）→ JSON
  → 更新 nb_topic_tracking（聊点状态机 + 情绪档）
  → 下一轮通过 AttentionPacket 注入主 Agent

**注意**：历史压缩已从此处移出，现由 SessionStore.maybe_compress() 同步触发
（在主 Agent LLM 调用前），避免后台 worker 反摸主 Agent 内存。
被丢弃消息的 LLM 记忆提取在 Flowcut/agent/memory_extract.py。
"""

from __future__ import annotations

import json
import hashlib
import time
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from simpleclaw.harness.hooks import PostrunHook, TurnContext
from simpleclaw.llm.base import LLMProvider
from simpleclaw.llm.chunks import TextChunk
from simpleclaw.runtime.task_protocol import TaskExecutionResult

# TODO: 实现后替换 — Flowcut.storage 暂时没有 topic_repo
# from Flowcut.storage.topic_repo import TopicRepository
TopicRepository = Any

_COLD_PATH_PROMPT_PATH = Path(__file__).parent.parent / "workspace" / "cold_path.md"
_SPLIT_MARKER = "===SPLIT_SYSTEM_USER==="

_MAX_DISCUSSING = 1
_MAX_PENDING    = 2
_MOOD_LABELS = {"high", "neutral", "low"}
_MOOD_DECAY_TURNS = 6
_VALID_PENDING_KINDS = {"postponed", "derived", "journey"}
_CACHE_EXPIRE_S = 2 * 24 * 60 * 60
_COLD_PATH_WINDOW_TURNS = 10


def _initial_topic_state(stage: str) -> dict[str, Any] | None:
    """为新用户生成初始 topic state（journey-stage 决定塞哪些 pending）。

    返回 None 表示不需要 seed。novice 阶段塞两条引导 pending：念咒语 / 上传自拍。
    探索期 / 成熟期不在这里 seed —— 那是另一个系统的活。
    """
    if stage != "novice":
        return None

    return {
        "topics": {
            "引导许愿": {
                "status": "pending",
                "pending_kind": "journey",
                "turns": 0,
                "last_turn": 0,
                "first_pending_turn": 0,
                "hook": "她还没说过自己想变成什么样的愿望，对话自然时温柔开放地问一句「最近有没有想悄悄变好的小目标呀？」之类，不施压、不催。",
            },
            "引导上传自拍": {
                "status": "pending",
                "pending_kind": "journey",
                "turns": 0,
                "last_turn": 0,
                "first_pending_turn": 0,
                "hook": "她还没拍正脸自拍，自然话题告一段落时陈述式邀请一次，不施压。",
            },
        },
        "mood": None,
        "total_turns": 0,
        "last_reminder_turn": 0,
        "last_memory_extract_turn": 0,
    }


def _load_split_prompt(path: Path) -> tuple[str, str]:
    """加载 cold_path.md，按 ===SPLIT_SYSTEM_USER=== 切成 (system, user_template)。

    system 是稳定指令、进 stable_prefix 享受 prefix cache；
    user_template 是带 placeholder 的动态部分，每轮填充。

    若文件不存在或没有分隔符，返回 ("", "")。
    """
    if not path.exists():
        return "", ""
    text = path.read_text(encoding="utf-8").strip()
    if _SPLIT_MARKER not in text:
        # 兼容老文件（没分隔标记）——全部当作系统提示
        return text, ""
    system_part, user_part = text.split(_SPLIT_MARKER, 1)
    return system_part.strip(), user_part.strip()


def _load_prompt(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


class ColdPathHook(PostrunHook):
    """每轮对话结束后：指令跟随（聊点 + 情绪）。

    **不再负责历史压缩**——压缩已搬到 SessionStore.maybe_compress()，
    由主 Agent 同步触发，以避免后台 worker 反摸主 Agent 内存的怪异耦合。

    参数说明
    --------
    topic_key_fn:   从 TurnContext 计算 nb_topic_tracking 主键的函数。
                    默认 lambda ctx: ctx.tenant_key（主 Agent 行为）。
                    子 Agent 传 lambda ctx: ctx.session_key 以实现隔离。
    """

    def __init__(
        self,
        llm: LLMProvider,
        topic_repo: TopicRepository,
        *,
        cache_repo: Any | None = None,
        cache_window_turns: int = _COLD_PATH_WINDOW_TURNS,
        topic_key_fn: Callable[[TurnContext], str] | None = None,
        initial_state_fn: Callable[[str], Any] | None = None,
    ) -> None:
        self._llm = llm
        self._topic_repo = topic_repo
        self._cache_repo = cache_repo
        self._cache_window_turns = max(1, int(cache_window_turns or _COLD_PATH_WINDOW_TURNS))
        self._topic_key_fn: Callable[[TurnContext], str] = topic_key_fn or (lambda ctx: ctx.tenant_key)
        # 当 topic_repo.get 返回 None 时，调用此函数获取 seed 状态（异步，
        # 由调用方提供 stage 查询逻辑）。返回 None 表示不 seed。
        self._initial_state_fn = initial_state_fn
        # prompt 分两段：system（稳定指令，走 prefix cache）+ user（动态数据）
        self._system_prompt, self._user_template = _load_split_prompt(_COLD_PATH_PROMPT_PATH)
        self._prompt_fingerprint = _hash(self._system_prompt)

    async def on_turn_end(self, ctx: TurnContext) -> TaskExecutionResult:
        try:
            return await self._run(ctx)
        except Exception as exc:
            logger.warning(
                "冷路径执行失败 tenant={} session={}：{}",
                ctx.tenant_key, ctx.session_key, exc,
            )
            return TaskExecutionResult.failed(
                f"cold path failed: {exc}",
                summary="cold path execution failed",
            )

    async def _run(self, ctx: TurnContext) -> TaskExecutionResult:
        # 每轮必跑：指令跟随（压缩逻辑已移出）
        topic_tracking_updated = await self._run_topic_tracking(ctx)
        if topic_tracking_updated:
            return TaskExecutionResult.succeeded(
                summary="topic tracking updated",
                details={"topic_tracking_updated": True},
            )
        return TaskExecutionResult.noop(
            summary="cold path completed with no state changes",
            details={"topic_tracking_updated": False},
        )

    # ------------------------------------------------------------------
    # Task 1 · 指令跟随
    # ------------------------------------------------------------------

    async def _run_topic_tracking(self, ctx: TurnContext) -> bool:
        if not self._system_prompt or not self._user_template:
            return False

        topic_key = self._topic_key_fn(ctx)

        # 读取当前聊点状态；新用户首次进来时 seed 一份初始 state（含 journey pending）
        state = await self._topic_repo.get(topic_key)
        if state is None and self._initial_state_fn is not None:
            try:
                seeded = self._initial_state_fn(ctx.tenant_key)
                if hasattr(seeded, "__await__"):
                    seeded = await seeded
                state = seeded
            except Exception as exc:
                logger.warning("cold_path initial_state_fn failed: {}", exc)
        current_topics: dict[str, Any] = state["topics"] if state else {}
        current_mood: dict[str, Any] | None = state["mood"] if state else None
        total_turns = (state["total_turns"] if state else 0) + 1
        last_reminder = state["last_reminder_turn"] if state else 0
        last_extract = state["last_memory_extract_turn"] if state else 0

        user_content = _fill_user_template(
            template=self._user_template,
            current_topics=current_topics,
            current_mood=current_mood,
            user_message=ctx.user_message,
            assistant_reply=ctx.assistant_reply,
            media=ctx.media,
        )

        raw = await self._complete_topic_tracking(
            ctx=ctx,
            topic_key=topic_key,
            user_content=user_content,
        )
        if not raw:
            # 静默轮：仍需判断情绪衰减
            current_mood = _apply_mood_update(current_mood, None, total_turns)
            await self._topic_repo.upsert(
                topic_key, current_topics, total_turns,
                last_reminder, last_extract, mood=current_mood,
            )
            return True

        data = _parse_json_safe(raw)
        if data is None:
            raise RuntimeError(f"cold path topic tracking returned invalid JSON: {raw[:100]}")

        _apply_topic_update(current_topics, data, total_turns)
        current_mood = _apply_mood_update(current_mood, data.get("mood"), total_turns)

        await self._topic_repo.upsert(
            topic_key, current_topics, total_turns,
            last_reminder, last_extract, mood=current_mood,
        )
        logger.debug(
            "冷路径·指令跟随完成 topic_key={} turn={} discussing={} pending={}",
            topic_key, total_turns,
            [l for l, i in current_topics.items() if isinstance(i, dict) and i.get("status") == "discussing"],
            [l for l, i in current_topics.items() if isinstance(i, dict) and i.get("status") == "pending"],
        )
        return True

    async def _complete_topic_tracking(
        self,
        *,
        ctx: TurnContext,
        topic_key: str,
        user_content: str,
    ) -> str:
        """Run cold_path through a short no-tools Responses session window.

        The fallback keeps the previous prefix-cache behavior for non-Volcengine
        test doubles or if the session cache path fails.
        """
        if self._cache_repo is None or not hasattr(self._llm, "complete_session"):
            return await _llm_complete_system_user(
                self._llm,
                system=self._system_prompt,
                user=user_content,
                max_tokens=300,
            )

        try:
            return await _llm_complete_cold_path_session(
                self._llm,
                cache_repo=self._cache_repo,
                system=self._system_prompt,
                user=user_content,
                prompt_fingerprint=self._prompt_fingerprint,
                tenant_key=ctx.tenant_key,
                session_key=topic_key,
                max_tokens=300,
                window_turns=self._cache_window_turns,
            )
        except Exception as exc:
            logger.warning(
                "cold_path session cache failed tenant={} topic_key={} fallback=prefix err={}",
                ctx.tenant_key, topic_key, exc,
            )
            return await _llm_complete_system_user(
                self._llm,
                system=self._system_prompt,
                user=user_content,
                max_tokens=300,
            )


# ---------------------------------------------------------------------------
# 热路径注入：build_reminder
# ---------------------------------------------------------------------------

def build_reminder(state: dict[str, Any]) -> str:
    """将聊点追踪状态转换为注入主 Agent 的提示文本。

    返回空字符串表示本轮没有需要注入的内容。
    调用方将返回值包装成 AttentionPacket。
    """
    topics = state.get("topics", {})
    total_turns = int(state.get("total_turns") or 0)

    discussing_lines: list[str] = []
    pending_lines: list[str] = []

    _PENDING_KIND_LABEL = {"postponed": "用户暂缓", "derived": "本轮暴露的钩子", "journey": "新手期引导"}

    for label, info in topics.items():
        if not isinstance(info, dict):
            continue
        hook = str(info.get("hook") or "").strip()
        status = info.get("status")
        if status == "discussing":
            tail = f"：{hook}" if hook else ""
            discussing_lines.append(f"- {label}{tail}")
        elif status == "pending":
            kind = str(info.get("pending_kind") or "").strip().lower()
            kind_label = _PENDING_KIND_LABEL.get(kind, "代聊")
            first_turn = info.get("first_pending_turn")
            gap_note = (
                f"，已挂 {total_turns - int(first_turn)} 轮"
                if isinstance(first_turn, int) and total_turns > first_turn
                else ""
            )
            tail = f"：{hook}" if hook else ""
            pending_lines.append(f"- {label}（{kind_label}{gap_note}）{tail}")

    lines: list[str] = []
    if discussing_lines:
        lines.append("【当前正在聊】兜住、自然延续；以下是上一轮的进度抓手：")
        lines.extend(discussing_lines)
    if pending_lines:
        if lines:
            lines.append("")
        lines.append("【代聊点】**只在当前话题自然冷下来时**捞一个引——用陈述式过渡，不要硬切：")
        lines.extend(pending_lines)

    # 情绪指令（neutral 不注入）
    _MOOD_DIRECTIVES = {
        "low": (
            "当前用户状态偏【低能 / 累】。这轮回复要**轻、短、温暖**；"
            "只接情绪或共情，不要主动展开新话题，不要抛问题。"
        ),
        "high": (
            "当前用户状态偏【高能 / 兴奋】。可以稍微活泼、节奏跟上；"
            "如果她明确抛了开放话题，可以接得稍展开（但仍不要连续反问）。"
        ),
    }
    mood = state.get("mood")
    if isinstance(mood, dict):
        directive = _MOOD_DIRECTIVES.get(str(mood.get("label") or "").strip().lower())
        if directive:
            if lines:
                lines.append("")
            lines.append(directive)

    if not lines:
        return ""
    lines.append("不要提及或复述这条信息。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 聊点状态机
# ---------------------------------------------------------------------------

def _apply_topic_update(
    current_topics: dict[str, Any],
    actions: dict[str, Any],
    total_turns: int,
) -> None:
    """将冷路径输出的 discussing/pending/resolved/drop 应用到聊点字典。"""
    for item in actions.get("discussing") or []:
        label, hook, _ = _parse_topic_item(item)
        if not label:
            continue
        existing = current_topics.get(label)
        if isinstance(existing, dict):
            existing["status"] = "discussing"
            existing["turns"] = int(existing.get("turns") or 0) + 1
            existing["last_turn"] = total_turns
            if hook:
                existing["hook"] = hook
            existing.pop("pending_kind", None)
        else:
            current_topics[label] = {
                "status": "discussing", "turns": 1,
                "last_turn": total_turns, "hook": hook,
            }

    for item in actions.get("pending") or []:
        label, hook, kind = _parse_topic_item(item)
        if not label:
            continue
        normalized_kind = kind if kind in _VALID_PENDING_KINDS else "derived"
        existing = current_topics.get(label)
        if isinstance(existing, dict):
            existing["status"] = "pending"
            existing["last_turn"] = total_turns
            existing["pending_kind"] = normalized_kind
            if hook:
                existing["hook"] = hook
        else:
            current_topics[label] = {
                "status": "pending", "turns": 0,
                "last_turn": total_turns,
                "first_pending_turn": total_turns,
                "hook": hook, "pending_kind": normalized_kind,
            }

    # resolved 直接删除——已聊完的内容交给历史压缩写入长期记忆
    for label in actions.get("resolved") or []:
        if isinstance(label, str):
            current_topics.pop(label.strip(), None)

    for label in actions.get("drop") or []:
        if isinstance(label, str):
            current_topics.pop(label.strip(), None)

    # 安全上限：discussing 最多 1 个，pending 最多 2 个，超出淘汰最旧的
    discussing = [
        (lbl, info) for lbl, info in current_topics.items()
        if isinstance(info, dict) and info.get("status") == "discussing"
    ]
    if len(discussing) > _MAX_DISCUSSING:
        discussing.sort(key=lambda p: int(p[1].get("last_turn") or 0))
        for lbl, _ in discussing[:-_MAX_DISCUSSING]:
            current_topics.pop(lbl, None)

    pending = [
        (lbl, info) for lbl, info in current_topics.items()
        if isinstance(info, dict) and info.get("status") == "pending"
    ]
    if len(pending) > _MAX_PENDING:
        pending.sort(key=lambda p: int(p[1].get("last_turn") or 0))
        for lbl, _ in pending[:-_MAX_PENDING]:
            current_topics.pop(lbl, None)


def _parse_topic_item(item: Any) -> tuple[str, str, str]:
    if isinstance(item, str):
        return item.strip(), "", ""
    if not isinstance(item, dict):
        return "", "", ""
    return (
        str(item.get("label") or "").strip(),
        str(item.get("hook") or "").strip(),
        str(item.get("pending_kind") or "").strip().lower(),
    )


# ---------------------------------------------------------------------------
# 情绪状态机
# ---------------------------------------------------------------------------

def _apply_mood_update(
    current_mood: dict[str, Any] | None,
    new_mood: Any,
    total_turns: int,
) -> dict[str, Any] | None:
    """合并新情绪信号与已有情绪档，含粘性和衰减规则。"""
    incoming_label: str | None = None
    incoming_signals: list[str] = []

    if isinstance(new_mood, dict):
        label_raw = str(new_mood.get("label") or "").strip().lower()
        if label_raw in _MOOD_LABELS:
            incoming_label = label_raw
        sigs = new_mood.get("signals")
        if isinstance(sigs, list):
            incoming_signals = [str(s).strip() for s in sigs if s][:2]

    if incoming_label is None:
        if not isinstance(current_mood, dict):
            return None
        last_signal = int(current_mood.get("last_signal_turn") or 0)
        if last_signal and total_turns - last_signal >= _MOOD_DECAY_TURNS:
            if str(current_mood.get("label") or "") == "neutral":
                return current_mood
            return {
                "label": "neutral", "since_turn": total_turns,
                "last_signal_turn": last_signal, "signals": [],
            }
        return current_mood

    prev_label = str(current_mood.get("label") or "") if isinstance(current_mood, dict) else ""
    since_turn = (
        int(current_mood.get("since_turn") or total_turns)
        if isinstance(current_mood, dict) and prev_label == incoming_label
        else total_turns
    )
    return {
        "label": incoming_label,
        "since_turn": since_turn,
        "last_signal_turn": total_turns,
        "signals": incoming_signals,
    }


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _fill_user_template(
    *,
    template: str,
    current_topics: dict[str, Any],
    current_mood: dict[str, Any] | None,
    user_message: str,
    assistant_reply: str,
    media: list[str] | None = None,
) -> str:
    """填充 user 侧模板——system 侧不含任何 placeholder，不需要替换。"""
    topics_str = json.dumps(
        [
            {
                "label": lbl,
                "status": info.get("status", "discussing"),
                "turns": info.get("turns", 0),
                "hook": info.get("hook", ""),
                **({"pending_kind": info["pending_kind"]} if info.get("pending_kind") else {}),
            }
            for lbl, info in current_topics.items()
            if isinstance(info, dict)
        ],
        ensure_ascii=False,
    ) if current_topics else "[]"

    mood_str = json.dumps(current_mood, ensure_ascii=False) if current_mood else "null"

    media_signal = f"用户在本轮上传了 {len(media)} 张图片" if media else "无"

    return (
        template
        .replace("<<<CURRENT_TOPICS>>>", topics_str)
        .replace("<<<CURRENT_MOOD>>>", mood_str)
        .replace("<<<PROFILE_SECTION>>>", "")
        .replace("<<<MEDIA_SIGNAL>>>", media_signal)
        .replace("<<<USER_MESSAGE>>>", user_message)
        .replace("<<<ASSISTANT_REPLY>>>", assistant_reply)
    )


async def _llm_complete_system_user(
    llm: LLMProvider,
    *,
    system: str,
    user: str,
    max_tokens: int,
    cache_tenant_key: str = "__default__",
) -> str:
    """system + user 两条消息的非流式消费器。

    带 _cache_stable_prefix / _cache_tenant_key hint，让 VolcengineLLM 能走 prefix cache。
    所有调用方（cold_path topic tracking）共用 __default__ tenant——cold path 指令对
    所有租户一致，共享同一缓存槽即可。
    """
    parts: list[str] = []
    async for chunk in llm.stream_with_retry(
        [
            {
                "role": "system",
                "content": system,
                "_cache_stable_prefix": system,
                "_cache_dynamic_tail": "",
                "_cache_tenant_key": cache_tenant_key,
                "_cache_lane": "cold_path_prefix_legacy",
            },
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=0.0,
    ):
        if isinstance(chunk, TextChunk):
            parts.append(chunk.token)
    return "".join(parts).strip()


async def _llm_complete_cold_path_session(
    llm: LLMProvider,
    *,
    cache_repo: Any,
    system: str,
    user: str,
    prompt_fingerprint: str,
    tenant_key: str,
    session_key: str,
    max_tokens: int,
    window_turns: int,
) -> str:
    """Collect cold_path output using a short Responses session-cache window."""
    config = getattr(llm, "config", None)
    model = str(getattr(config, "model", "") or "unknown")
    thinking_type = _thinking_type(config)
    record = await cache_repo.get_session_cache(
        provider="volcengine",
        lane="cold_path",
        tenant_key=tenant_key,
        session_key=session_key,
        model=model,
        thinking_type=thinking_type,
        cache_mode="session_window",
        prompt_fingerprint=prompt_fingerprint,
        context_version=0,
    )
    cache_hit = record is not None and record.turn_count < window_turns
    previous_response_id = record.response_id if cache_hit else None
    input_items = (
        [{"type": "message", "role": "user", "content": user}]
        if cache_hit
        else [
            {"type": "message", "role": "system", "content": system},
            {"type": "message", "role": "user", "content": user},
        ]
    )
    logger.info(
        "cold_path session start tenant={} session={} cache_hit={} turn_count={} input_items={} prev_resp={}",
        tenant_key,
        session_key,
        cache_hit,
        record.turn_count if record else 0,
        len(input_items),
        (previous_response_id or "")[:24],
    )

    complete_session = getattr(llm, "complete_session")
    response = await complete_session(
        input_items=input_items,
        previous_response_id=previous_response_id,
        max_tokens=max_tokens,
        temperature=0.0,
    )
    response_id = str(getattr(response, "response_id", "") or "")
    output_text = str(getattr(response, "output_text", "") or "").strip()
    if not response_id:
        raise RuntimeError("cold_path session response returned no response_id")

    base_response_id = (record.base_response_id or record.response_id) if cache_hit and record else response_id
    turn_count = (record.turn_count if cache_hit and record else 0) + 1
    await cache_repo.upsert_session_cache(
        provider="volcengine",
        lane="cold_path",
        tenant_key=tenant_key,
        session_key=session_key,
        model=model,
        thinking_type=thinking_type,
        cache_mode="session_window",
        prompt_fingerprint=prompt_fingerprint,
        context_version=0,
        main_consolidated_from=0,
        context_fingerprint=_hash(user),
        response_id=response_id,
        base_response_id=base_response_id,
        turn_count=turn_count,
        expire_at=int(time.time()) + _CACHE_EXPIRE_S,
        metadata={
            "cache_hit": cache_hit,
            "window_turns": window_turns,
            "window_reset": bool(record and not cache_hit),
            "input_chars": len(user),
        },
    )
    logger.info(
        "cold_path session done tenant={} session={} cache_hit={} turn_count={} chars={} resp_id={}",
        tenant_key, session_key, cache_hit, turn_count, len(output_text), response_id[:24],
    )
    return output_text


async def _llm_complete(llm: LLMProvider, prompt: str, max_tokens: int) -> str:
    """收集 LLM 全部输出（非流式消费）。"""
    parts: list[str] = []
    async for chunk in llm.stream_with_retry(
        [{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.0,
    ):
        if isinstance(chunk, TextChunk):
            parts.append(chunk.token)
    return "".join(parts).strip()


def _hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode()).hexdigest()


def _thinking_type(config: Any) -> str:
    return "enabled" if bool(getattr(config, "thinking", False)) else "disabled"


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
            pass
    return None
