"""场景定义 — 每个 Scenario 是一个完整的业务流程验证。

断言策略：
  - 断言「结构」（有无回复 / 数据是否写入）而非 LLM 具体文本
  - background task 等待时间可通过 --wait-bg 控制（默认 8s）
  - 每个 assert 失败时打印期望 vs 实际值，方便排查
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable, Awaitable

from tests.scaffold.client import ScaffoldClient


# ------------------------------------------------------------------
# 断言结果
# ------------------------------------------------------------------

@dataclass
class AssertResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ScenarioResult:
    scenario: str
    asserts: list[AssertResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(a.passed for a in self.asserts)

    @property
    def failed_count(self) -> int:
        return sum(1 for a in self.asserts if not a.passed)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.asserts.append(AssertResult(name, passed, detail))

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        self.add(name, condition, detail)
        return condition


# ------------------------------------------------------------------
# 基础类
# ------------------------------------------------------------------

class ScenarioBase:
    name: str = "unnamed"
    description: str = ""

    def __init__(self, client: ScaffoldClient, user_id: str, wait_bg: float = 8.0):
        self.c = client
        self.uid = user_id
        self.wait_bg = wait_bg

    async def run(self) -> ScenarioResult:
        raise NotImplementedError


# ------------------------------------------------------------------
# S01: 主 Agent 基础对话 + 后台任务验证
# ------------------------------------------------------------------

class S01MainAgentBasic(ScenarioBase):
    name = "S01_main_agent_basic"
    description = "主 Agent 发消息 → SSE 回复 → 消息持久化 → postprocess / cold_path 后台触发"

    async def run(self) -> ScenarioResult:
        r = ScenarioResult(self.name)
        session_key = f"main:{self.uid}"

        # ── Step 1: 发消息，验证 SSE ──────────────────────────────
        result = await self.c.chat(self.uid, "你好，介绍一下你自己")
        r.check("SSE 流收到 chunk",   len(result.chunks) > 0,
                f"chunks={len(result.chunks)}")
        r.check("SSE 流收到 done",    result.done,
                "未收到 done 事件")
        r.check("回复非空",           bool(result.full_reply),
                f"full_reply len={len(result.full_reply)}")
        r.check("无 error 事件",      result.error is None,
                f"error={result.error}")

        if not result.ok:
            return r  # 流都没通，后续断言无意义

        # ── Step 2: 消息持久化 ──────────────────────────────────
        await asyncio.sleep(1.0)
        msgs = await self.c.get_session_history(self.uid, session_key)
        user_msgs = [m for m in msgs if m["role"] == "user"]
        asst_msgs = [m for m in msgs if m["role"] == "assistant"]
        r.check("用户消息已写入 DB",      len(user_msgs) >= 1,
                f"user_msgs={len(user_msgs)}")
        r.check("助手消息已写入 DB",      len(asst_msgs) >= 1,
                f"asst_msgs={len(asst_msgs)}")
        r.check("助手回复与 SSE 一致",
                asst_msgs[-1]["content"] == result.full_reply if asst_msgs else False,
                "DB 内容与 SSE 不一致")

        # ── Step 3: 等待 postprocess / cold_path ─────────────────
        await asyncio.sleep(self.wait_bg)

        docs = await self.c.get_tenant_docs(self.uid)
        state = await self.c.get_dynamic_state(self.uid)

        r.check("USER.md 已存在（postprocess 运行）",
                bool(docs.get("user_md")),
                f"user_md len={len(docs.get('user_md',''))}")
        r.check("话题状态已写入（cold_path 运行）",
                state.get("topic_state") is not None,
                f"topic_state={state.get('topic_state')}")
        if state.get("topic_state"):
            total = state["topic_state"].get("total_turns", 0)
            r.check("total_turns >= 1", total >= 1, f"total_turns={total}")

        return r


# ------------------------------------------------------------------
# S02: 连续对话 — 验证历史上下文被携带
# ------------------------------------------------------------------

class S02MainAgentMultiTurn(ScenarioBase):
    name = "S02_main_agent_multi_turn"
    description = "连续两轮对话 → 验证第二轮 Agent 能引用第一轮内容"

    async def run(self) -> ScenarioResult:
        r = ScenarioResult(self.name)
        session_key = f"main:{self.uid}"

        # 第一轮
        r1 = await self.c.chat(self.uid, "我最近皮肤有点干燥")
        r.check("第一轮 SSE ok", r1.ok, f"error={r1.error}")
        if not r1.ok:
            return r

        await asyncio.sleep(1.0)

        # 第二轮 — 用指代词，Agent 必须依赖上文才能正确回答
        r2 = await self.c.chat(self.uid, "你刚才说到的问题，有没有推荐产品")
        r.check("第二轮 SSE ok", r2.ok, f"error={r2.error}")
        r.check("第二轮回复非空", bool(r2.full_reply),
                f"len={len(r2.full_reply)}")

        # 验证 DB 里有两轮用户消息
        await asyncio.sleep(0.5)
        msgs = await self.c.get_session_history(self.uid, session_key, limit=40)
        user_msgs = [m for m in msgs if m["role"] == "user"]
        r.check("DB 存有 2 条用户消息", len(user_msgs) >= 2,
                f"user_msgs={len(user_msgs)}")

        return r


# ------------------------------------------------------------------
# S03: 肌肤日记子 Agent
# ------------------------------------------------------------------

class S03SkinDiarySubagent(ScenarioBase):
    name = "S03_skin_diary_subagent"
    description = "skin_diary session → 子 Agent 路由 → 回复 → 消息持久化 → 子 Agent 后台任务"

    async def run(self) -> ScenarioResult:
        r = ScenarioResult(self.name)
        sd_session = f"skin_diary:{self.uid}"

        # ── Step 1: 发消息到子 Agent ────────────────────────────
        result = await self.c.chat(self.uid, "我想了解一下我的肌肤状况", session_id=sd_session)
        r.check("SSE chunk", len(result.chunks) > 0, f"chunks={len(result.chunks)}")
        r.check("SSE done",  result.done, "未收到 done")
        r.check("回复非空",  bool(result.full_reply), "")
        r.check("无 error",  result.error is None, f"error={result.error}")
        if not result.ok:
            return r

        # ── Step 2: 消息写入子 Agent session ────────────────────
        await asyncio.sleep(1.0)
        msgs = await self.c.get_session_history(self.uid, sd_session)
        r.check("子 Agent 消息已持久化",
                any(m["role"] == "user" for m in msgs),
                f"msgs={len(msgs)}")

        # ── Step 3: 等待子 Agent 后台任务 ────────────────────────
        await asyncio.sleep(self.wait_bg)

        # 子 Agent 的 topic 状态用 session_key 作为 key（"skin_diary:{uid}"）
        state = await self.c.get_dynamic_state(sd_session)
        r.check("子 Agent 话题状态写入",
                state.get("topic_state") is not None,
                f"topic_state={state.get('topic_state')}")

        return r


# ------------------------------------------------------------------
# S04: Journey 阶段升级
# ------------------------------------------------------------------

class S04JourneyPromotion(ScenarioBase):
    name = "S04_journey_promotion"
    description = "POST /journey/event → stage 升级为 explore"

    async def run(self) -> ScenarioResult:
        r = ScenarioResult(self.name)

        before = await self.c.get_tenant_docs(self.uid)
        stage_before = before.get("journey_stage", "novice")

        result = await self.c.journey_event(self.uid, "explore_entered")
        r.check("journey_event 接口返回 ok",
                result.get("ok") is True,
                f"result={result}")
        r.check("阶段已升级（promoted=true 或 stage 变化）",
                result.get("promoted") is True or result.get("stage_after") != stage_before,
                f"stage_before={stage_before} stage_after={result.get('stage_after')}")

        return r


# ------------------------------------------------------------------
# S05: Admin Prompt 读写
# ------------------------------------------------------------------

class S05AdminPromptReadWrite(ScenarioBase):
    name = "S05_admin_prompt_rw"
    description = "GET /admin/prompt + PUT /admin/prompt → 保存再读回内容一致"

    async def run(self) -> ScenarioResult:
        r = ScenarioResult(self.name)

        data = await self.c.get_prompt("agent")
        r.check("Agent.md 可读取", bool(data.get("content")),
                f"content len={len(data.get('content',''))}")
        if not data.get("content"):
            return r

        original = data["content"]
        ok = await self.c.put_prompt("agent", original)
        r.check("Agent.md 写入返回 204", ok, "写入失败")

        data2 = await self.c.get_prompt("agent")
        r.check("写入后读回内容一致",
                data2.get("content") == original,
                "内容不一致")

        return r


# ------------------------------------------------------------------
# S06: 服务健康检查
# ------------------------------------------------------------------

class S06HealthCheck(ScenarioBase):
    name = "S06_health"
    description = "GET /health → 服务正常"

    async def run(self) -> ScenarioResult:
        r = ScenarioResult(self.name)
        ok = await self.c.health()
        r.check("/health 返回 200", ok, "服务不可达")
        return r


# ------------------------------------------------------------------
# 场景注册表
# ------------------------------------------------------------------

ALL_SCENARIOS: list[type[ScenarioBase]] = [
    S06HealthCheck,
    S05AdminPromptReadWrite,
    S01MainAgentBasic,
    S02MainAgentMultiTurn,
    S03SkinDiarySubagent,
    S04JourneyPromotion,
]
