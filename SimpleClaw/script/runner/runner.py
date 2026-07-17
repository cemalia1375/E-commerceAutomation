"""Scenario runner entrypoint."""

from __future__ import annotations

import argparse
import asyncio

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - local runner is POSIX in our dev env
    fcntl = None

import yaml
from loguru import logger

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simpleclaw.core.events import DoneEvent, ErrorEvent, TextEvent, ToolResultEvent
from simpleclaw.core.loop import _build_user_content
from simpleclaw.core.messages import AssistantMessage, ToolCall, UserMessage
from simpleclaw.context import AttentionPacket

from Mojing.api.container import build_container
from Mojing.agent.capabilities import capabilities_from_device_context
from Mojing.agent.first_token import (
    _build_shared_context,
    _infer_agent_lane,
    _normalize_agent_lane,
    build_first_token_continuation_instruction,
    build_first_token_user_message,
    join_first_token_reply,
)
from Mojing.api.routes.chat import (
    _record_uploaded_images,
    _resolve_opener_text,
)
from Mojing.runtime.post_turn import enqueue_post_turn_tasks
from Mojing.runtime.turn_facts import collect_turn_facts
from Mojing.runtime.obligations import (
    DEPENDENCY_CABINET_PRODUCT_RESEARCH_SUCCEEDED,
    dispatch_obligations_for_dependency,
)
from Mojing.storage.memory_repo import MySQLMemory
from Mojing.tools.image_tools import build_image_analysis_envelope
from Mojing.tools.device_command import DeviceCommandTool, normalize_device_command
from simpleclaw.tools.base import ToolResult

from simpleclaw.dream import DreamAdmissionContext, DreamCandidate
from Mojing.dream.signals import memory_ledger_applied_signal

from script.runner.memory_snapshot import render_memory_snapshot_md
from script.runner.assertions import evaluate_checks
from script.runner.capture import TurnCapture, wrap_all_tool_registries, wrap_tool_registry
from script.runner.logger import RunLogger
from script.runner.memory_watch import install_memory_watch
from script.runner.dream_watch import install_dream_watch
from script.runner.reporter import write_report

_RUNNER_LOCK = asyncio.Lock()


@asynccontextmanager
async def _main_session_turn_guard(container, session_key: str):
    """Keep runner direct loop serialized with production session/activation locks."""
    session_lock = container.sessions.get_lock(session_key)
    await session_lock.acquire()
    scheduler = getattr(getattr(container, "main_session_ingress", None), "scheduler", None)
    set_busy = getattr(scheduler, "_set_busy", None)
    if set_busy is not None:
        await set_busy(session_key, True)
    try:
        yield
    finally:
        if set_busy is not None:
            await set_busy(session_key, False)
        session_lock.release()


async def run_scenario(path: Path, *, run_dir: Path | None = None) -> dict[str, Any]:
    scenario = yaml.safe_load(path.read_text(encoding="utf-8"))
    return await run_scenario_dict(scenario, run_dir=run_dir, source=str(path))


async def run_scenario_dict(
    scenario: dict[str, Any],
    *,
    run_dir: Path | None = None,
    source: str = "inline",
    container: Any | None = None,
) -> dict[str, Any]:
    async with _RUNNER_LOCK:
        return await _run_scenario_dict(
            scenario,
            run_dir=run_dir,
            source=source,
            container=container,
        )


async def _run_scenario_dict(
    scenario: dict[str, Any],
    *,
    run_dir: Path | None = None,
    source: str = "inline",
    container: Any | None = None,
) -> dict[str, Any]:
    agent = str(scenario.get("agent") or "main").strip()
    if agent not in {"main", "skin_diary", "deep_report"}:
        raise NotImplementedError(f"Scenario runner does not support agent={agent!r}")

    scenario_id = str(scenario.get("id") or f"scenario_{datetime.now().strftime('%Y%m%d%H%M%S')}")
    run_dir = run_dir or _default_run_dir(scenario_id)
    log = RunLogger(run_dir / f"{scenario_id}.log")
    log.write(f"SCENARIO START {scenario_id} source={source}")

    owns_container = container is None
    if owns_container:
        _isolate_task_queue_for_runner()
        container = await build_container()
    tenant_key = scenario.get("tenant_key") or _default_tenant_key(scenario_id)
    session_key = scenario.get("session_key") or _default_session_key(agent, tenant_key)
    stage = scenario.get("initial_stage") or "novice"
    default_prompt_surface, default_device_id, default_device_code = _resolve_device_context(scenario)
    restore_device_command = _install_device_command_mock(default_prompt_surface, scenario)
    memory_watch = None
    dream_watch = None

    try:
        await container.tenant_state_repo.save_journey(
            tenant_key,
            {"stage": stage, "milestones": {}},
        )
        await _apply_seed(container, tenant_key=tenant_key, session_key=session_key, scenario=scenario)
        # seed 之后再装监控钩子，seed 写入不计入 memory 变化
        memory_watch = install_memory_watch(log, tenant_key=tenant_key)
        dream_watch = install_dream_watch(log, tenant_key=tenant_key)
        await _start_scenario_session_listeners(
            container,
            tenant_key=tenant_key,
            default_session_key=session_key,
            scenario=scenario,
        )

        turns: list[dict[str, Any]] = []
        for index, turn in enumerate(scenario.get("turns") or [], start=1):
            if turn.get("media"):
                turn = {**turn, "media": _resolve_mock_refs(turn.get("media"), scenario)}
            turn_agent = str(turn.get("agent") or agent).strip()
            if turn_agent not in {"main", "skin_diary", "deep_report"}:
                raise NotImplementedError(f"Scenario runner does not support turn agent={turn_agent!r}")
            turn_session_key = _resolve_turn_session_key(
                turn.get("session_key"),
                agent=turn_agent,
                tenant_key=tenant_key,
                default=session_key if turn_agent == agent else None,
            )
            # 更新 turn 指针：本 turn（含其等待窗口）内发生的 memory 写入归属于该 turn
            if turn.get("scenario_action"):
                sa = turn.get("scenario_action")
                action_name = str(sa.get("name") if isinstance(sa, dict) else sa).strip()
                phase_tag = "dream" if action_name == "run_dream_now" else f"{index}:scenario_action"
                memory_watch.set_phase(phase_tag)
                dream_watch.set_phase(phase_tag)
            else:
                memory_watch.set_phase(str(index))
                dream_watch.set_phase(str(index))
            result = await _run_turn(
                container,
                agent=turn_agent,
                tenant_key=tenant_key,
                session_key=turn_session_key,
                turn=turn,
                index=index,
                default_wait_s=float(scenario.get("wait_side_effects_s") or 0),
                default_prompt_surface=default_prompt_surface,
                default_device_id=default_device_id,
                default_device_code=default_device_code,
            )
            result.setdefault("agent", turn_agent)
            result.setdefault("session_key", turn_session_key)
            hard = evaluate_checks(turn.get("hard_assertions"), result)
            soft = evaluate_checks(turn.get("soft_checks"), result)
            result["hard_assertions"] = [r.__dict__ for r in hard]
            result["soft_checks"] = [r.__dict__ for r in soft]
            result["verdict"] = "PASS" if all(r.passed for r in hard) else "FAIL"
            turns.append(result)

            log.write(
                "TURN {idx} phase={phase} measure={measure} verdict={verdict} "
                "ttft={ttft}ms total={total}ms tools={tools}".format(
                    idx=index,
                    phase=result.get("phase"),
                    measure=result.get("measure"),
                    verdict=result["verdict"],
                    ttft=result.get("ttft_ms"),
                    total=result.get("total_ms"),
                    tools=result.get("tools_called"),
                )
            )
            for item in _session_delta_assistant_log_lines(result):
                log.write(item)
            if "skin_before" in result:
                # dream 合并前后 skin 条目数；delta<0 = 多条冗余被收敛
                log.write(
                    "DREAM MERGE turn={idx} skin_before={b} skin_after={a} delta={d}".format(
                        idx=index,
                        b=result.get("skin_before"),
                        a=result.get("skin_after"),
                        d=result.get("skin_merge_delta"),
                    )
                )
            if result["verdict"] == "FAIL":
                for item in result["hard_assertions"]:
                    if not item["passed"]:
                        log.write(f"  FAIL {item['name']} {item['detail']}")

        memory_watch.set_phase("after_turns")
        dream_watch.set_phase("after_turns")
        log.write(memory_watch.summary_line())

        verdict = "PASS" if all(t["verdict"] == "PASS" for t in turns) else "FAIL"
        output = {
            "scenario": scenario_id,
            "agent": agent,
            "tenant_key": tenant_key,
            "session_key": session_key,
            "verdict": verdict,
            "turns": turns,
        }
        _write_turn_artifacts(run_dir, output)
        report_output = _compact_report_output(output)
        report_path = write_report(run_dir, report_output)
        output["report_path"] = str(report_path)
        report_output["report_path"] = str(report_path)
        try:
            snapshot_md = render_memory_snapshot_md(
                scenario_id=scenario_id,
                tenant_key=tenant_key,
                entries=await _memory_entries(container, tenant_key),
                ledgers=await _memory_ledgers(container, tenant_key),
                artifacts=await _dream_artifacts(container, tenant_key),
            )
            (run_dir / f"{scenario_id}.memory.md").write_text(snapshot_md, encoding="utf-8")
        except Exception as exc:
            log.write(f"MEMORY SNAPSHOT FAILED {exc}")
        log.write(f"SCENARIO END {scenario_id} verdict={verdict} report={report_path}")
        return report_output
    finally:
        if memory_watch is not None:
            memory_watch.uninstall()
        if dream_watch is not None:
            dream_watch.uninstall()
        if restore_device_command is not None:
            restore_device_command()
        if owns_container:
            await _shutdown_container(container)


def _default_session_key(agent: str, tenant_key: str) -> str:
    if agent == "main":
        return f"main:{tenant_key}"
    if agent == "skin_diary":
        return f"skin_diary:{tenant_key}"
    if agent == "deep_report":
        return f"deep_report:{tenant_key}"
    return f"{agent}:{tenant_key}"


def _default_tenant_key(scenario_id: str) -> str:
    """Generate a short test tenant key for external-service compatibility."""
    raw = f"{scenario_id}:{time.time_ns()}"
    digest = hashlib.blake2b(raw.encode("utf-8"), digest_size=4).hexdigest()
    return f"test_{digest}"


def _resolve_device_context(scenario: dict[str, Any]) -> tuple[str, Any, Any]:
    """从 scenario 推导 (prompt_surface, device_id, device_code)，对齐线上语义。

    线上 `/v1/chat/completions`（硬件端）固定走 device 表面，且 device_id/device_code
    取自请求的 `custom` 块。device 场景 YAML 也照此约定书写，因此这里：
    - prompt_surface：显式 `prompt_surface` 优先；否则当 protocol/endpoint 命中 v1 硬件入口时推断为 device。
    - device_id/device_code：先看顶层，再回落到 `custom` 块。
    """
    custom = scenario.get("custom")
    if not isinstance(custom, dict):
        custom = {}

    explicit_surface = str(scenario.get("prompt_surface") or "").strip().lower()
    if explicit_surface:
        surface = explicit_surface
    else:
        protocol = str(scenario.get("protocol") or "").strip().lower()
        endpoint = str(scenario.get("endpoint") or "").strip().lower()
        is_device_entry = protocol == "v1_chat_completions" or endpoint == "/v1/chat/completions"
        surface = "device" if is_device_entry else "app"

    device_id = (
        scenario.get("device_id")
        or scenario.get("deviceId")
        or custom.get("device_id")
        or custom.get("deviceId")
    )
    device_code = (
        scenario.get("device_code")
        or scenario.get("deviceCode")
        or custom.get("device_code")
        or custom.get("deviceCode")
    )
    return surface, device_id, device_code


def _resolve_mock_refs(value: Any, scenario: dict[str, Any]) -> Any:
    """把字符串里的 `{mock.<dotted.path>}` 占位符替换成 scenario `mock` 块里的真实值。

    例如 turn 的 `media: ["{mock.photo_url}"]` 会被换成 `mock.photo_url` 的真实 URL，
    让模型在 Phase-2 真正收到可读取的图片，而不是字面占位符。
    """
    mock = scenario.get("mock")
    if not isinstance(mock, dict):
        return value

    def _lookup(path: str) -> Any:
        node: Any = mock
        for key in path.split("."):
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return None
        return node

    def _sub_str(text: str) -> str:
        def _repl(m: "re.Match[str]") -> str:
            resolved = _lookup(m.group(1))
            return str(resolved) if resolved is not None else m.group(0)
        return re.sub(r"\{mock\.([\w.]+)\}", _repl, text)

    if isinstance(value, str):
        return _sub_str(value)
    if isinstance(value, list):
        return [_resolve_mock_refs(item, scenario) for item in value]
    return value


def _install_device_command_mock(prompt_surface: str, scenario: dict[str, Any] | None = None):
    """把 device_command 工具改成 mock 成功，避免无真实硬件的 device 场景打到 Java 后端报"设备不存在"。

    离线 runner 不打真实硬件，但 `capture_photo` mock 必须对齐真实 `DeviceCommandTool`
    的工具结果契约：返回 `photo_ready` / `photo_failed` / `photo_timeout`，不能造一个线上
    没有的中间态。默认模拟正常 15s 窗口内回图，其余设备指令返回 `action=executed` 成功。
    只在 device 表面安装；非 device 场景返回 None（app 表面本就没有设备工具）。
    """
    if str(prompt_surface or "").strip().lower() != "device":
        return None

    scenario = scenario or {}
    scenario_mock = scenario.get("mock") if isinstance(scenario.get("mock"), dict) else {}
    capture_mock = scenario_mock.get("capture_photo") if isinstance(scenario_mock.get("capture_photo"), dict) else {}
    capture_action = str(capture_mock.get("action") or "photo_ready").strip()
    if capture_action not in {"photo_ready", "photo_failed", "photo_timeout"}:
        capture_action = "photo_ready"
    capture_photo_url = str(
        capture_mock.get("photo_url")
        or capture_mock.get("photoUrl")
        or "https://example.test/mock-device-photo.jpg"
    ).strip()

    original_execute = DeviceCommandTool.execute
    capture_call_index = 0

    async def _mock_execute(self, command: str = "", params: dict[str, Any] | None = None) -> ToolResult:
        nonlocal capture_call_index
        normalized_command, normalized_params = normalize_device_command(command, params)
        if normalized_command == "capture_photo":
            capture_call_index += 1
            payload = {
                "ok": True,
                "action": capture_action,
                "command": normalized_command,
                "params": normalized_params,
                "captureRequestId": f"cap_mock_{capture_call_index}",
                "photoId": f"mock_photo_{capture_call_index}",
            }
            if capture_action == "photo_ready":
                payload.update({
                    "photoUrl": capture_photo_url,
                    "cleanPhotoUrl": capture_photo_url,
                    "message_focus": (
                        "照片已经返回。不要再次调用 capture_photo；"
                        "交由业务层立刻开启带 image_url 的内部视觉回复轮。"
                    ),
                })
            elif capture_action == "photo_failed":
                payload.update({
                    "status": "failed",
                    "reason": str(capture_mock.get("reason") or "mock_photo_failed"),
                    "message_focus": (
                        "硬件已经明确返回拍照失败。请自然告诉用户这张没拍下来，"
                        "不要说已经拍好，也不要调用 analyze_image。若这是首次失败可轻量重试；"
                        "若用户本轮是在重试或前文已失败过，请改用手机清晰面部照兜底。"
                    ),
                })
            else:
                payload.update({
                    "status": "timeout",
                    "timeout_s": float(capture_mock.get("timeout_s") or 15.0),
                    "message_focus": (
                        "拍照动作已触发，但图片没有在等待窗口内返回。"
                        "请自然告诉用户这张图片暂时没回来，需要重新拍照；"
                        "不要再次调用 capture_photo。"
                    ),
                })
            return ToolResult(content=json.dumps(payload, ensure_ascii=False))
        else:
            message_focus = "设备指令已经执行成功。请用自然简短的话告诉用户已经弄好了。"
            action = "executed"
        payload = {
            "ok": True,
            "action": action,
            "command": normalized_command,
            "params": normalized_params,
            "message_focus": message_focus,
        }
        return ToolResult(content=json.dumps(payload, ensure_ascii=False))

    DeviceCommandTool.execute = _mock_execute

    def _restore() -> None:
        DeviceCommandTool.execute = original_execute

    return _restore


def _resolve_turn_session_key(
    raw: Any,
    *,
    agent: str,
    tenant_key: str,
    default: str | None = None,
) -> str:
    if raw:
        return str(raw).replace("{tenant_key}", str(tenant_key))
    return default or _default_session_key(agent, tenant_key)


def _isolate_task_queue_for_runner() -> None:
    """Use an in-process task queue by default so scenario runs are deterministic."""
    use_redis = os.getenv("SCENARIO_RUNNER_USE_REDIS", "").strip().lower()
    if use_redis in {"1", "true", "yes", "on"}:
        return
    os.environ["REDIS_URL"] = ""


async def _apply_seed(container, *, tenant_key: str, session_key: str, scenario: dict[str, Any]) -> None:
    seed = scenario.get("seed") or {}
    if not isinstance(seed, dict):
        return

    if seed.get("from_snapshot"):
        await _seed_from_snapshot(
            container,
            tenant_key=tenant_key,
            session_key=session_key,
            spec=seed.get("from_snapshot"),
        )

    docs = seed.get("docs") or {}
    if isinstance(docs, dict):
        for name, content in docs.items():
            await container.doc_repo.set(tenant_key, str(name), str(content or ""))

    for item in seed.get("memories") or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "main")
        topic = str(item.get("topic") or "seed")
        content = str(item.get("content") or "")
        if content:
            await MySQLMemory(container.db, tenant_key, source=source).store(
                topic,
                content,
                description=str(item.get("description") or ""),
                memory_type=str(item.get("memory_type") or "chitchat"),
            )

    if seed.get("skin_profile"):
        await _seed_skin_profile(
            container,
            tenant_key=tenant_key,
            session_key=session_key,
            spec=seed.get("skin_profile"),
        )

    skin_profiles = seed.get("skin_profiles") or []
    if isinstance(skin_profiles, dict):
        skin_profiles = [skin_profiles]
    for item in skin_profiles:
        await _seed_skin_profile(
            container,
            tenant_key=tenant_key,
            session_key=session_key,
            spec=item,
        )

    if seed.get("memory_ledger"):
        await _seed_memory_ledger(
            container,
            tenant_key=tenant_key,
            session_key=session_key,
            spec=seed.get("memory_ledger"),
        )

    if seed.get("skin_diary_result"):
        await _seed_skin_diary_result(
            container,
            tenant_key=tenant_key,
            spec=seed.get("skin_diary_result"),
        )

    images = seed.get("images") or seed.get("image_jobs") or []
    if isinstance(images, (str, dict)):
        images = [images]
    for item in images:
        await _seed_image_job(container, tenant_key=tenant_key, session_key=session_key, spec=item)

    if seed.get("deep_report"):
        await _seed_deep_report(
            container,
            tenant_key=tenant_key,
            session_key=session_key,
            spec=seed.get("deep_report"),
        )

    cabinet_products = seed.get("skincare_cabinet_products") or []
    if isinstance(cabinet_products, dict):
        cabinet_products = [cabinet_products]
    for item in cabinet_products:
        await _seed_skincare_cabinet_product(container, tenant_key=tenant_key, spec=item)


async def _seed_from_snapshot(container, *, tenant_key: str, session_key: str, spec: Any) -> None:
    """Clone a real tenant snapshot into this runner's isolated test tenant."""
    if not isinstance(spec, dict):
        raise ValueError("seed.from_snapshot must be an object")

    src_tenant = _first_seed_text(
        spec,
        "tenant",
        "tenant_id",
        "tenantId",
        "tenantid",
        "src_tenant",
        "tenant_key",
        "user",
        "user_id",
        "userId",
        "userid",
    )
    src_session = _first_seed_text(
        spec,
        "session",
        "session_id",
        "sessionId",
        "sessionid",
        "src_session",
        "session_key",
    )
    src_session = _normalize_source_session_key(src_session)
    cutoff_raw = _first_seed_value(spec, "msg_seq_cutoff", "msgSeqCutoff", "seq_cutoff", "cutoff")
    snapshot_at = _normalize_snapshot_at(
        _first_seed_text(spec, "snapshot_at", "snapshotAt", "snapshot_time", "snapshotTime")
    )

    if not src_tenant:
        raise ValueError("seed.from_snapshot requires tenant")
    if not src_session:
        raise ValueError("seed.from_snapshot requires session")
    if cutoff_raw is None:
        if not snapshot_at:
            raise ValueError("seed.from_snapshot requires msg_seq_cutoff or snapshot_at")
        msg_seq_cutoff = await _resolve_snapshot_cutoff(
            container.db,
            src_tenant=src_tenant,
            src_session=src_session,
            snapshot_at=snapshot_at,
            inclusive=_seed_bool(spec.get("include_snapshot_message", False)),
        )
    else:
        try:
            msg_seq_cutoff = int(cutoff_raw)
        except Exception as exc:
            raise ValueError("seed.from_snapshot requires integer msg_seq_cutoff") from exc
    if msg_seq_cutoff <= 0:
        raise ValueError("seed.from_snapshot.msg_seq_cutoff must be > 0")
    if tenant_key == src_tenant:
        raise ValueError("seed.from_snapshot destination tenant must differ from source tenant")

    from script.clone_tenant_snapshot import clone_to_tenant

    logger.info(
        "runner seed.from_snapshot: src={}/{} cutoff={} dst={}/{}",
        src_tenant,
        src_session,
        msg_seq_cutoff,
        tenant_key,
        session_key,
    )
    await clone_to_tenant(
        container.db,
        src_tenant=src_tenant,
        src_session=src_session,
        dst_tenant=tenant_key,
        dst_session=session_key,
        msg_seq_cutoff=msg_seq_cutoff,
        snapshot_at=snapshot_at or None,
        profile_limit=int(spec.get("profile_limit") or 3),
        diary_limit=int(spec.get("diary_limit") or 2),
        image_limit=int(spec.get("image_limit") or 3),
        force=_seed_bool(spec.get("force", False)),
    )


async def _resolve_snapshot_cutoff(
    db,
    *,
    src_tenant: str,
    src_session: str,
    snapshot_at: str,
    inclusive: bool = False,
) -> int:
    op = "<=" if inclusive else "<"
    async with db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT seq
                FROM nb_session_messages
                WHERE tenant_key=%s AND session_key=%s AND created_at {op} %s
                ORDER BY seq DESC
                LIMIT 1
                """,
                (src_tenant, src_session, snapshot_at),
            )
            row = await cur.fetchone()
    if not row:
        raise ValueError(
            "seed.from_snapshot cannot resolve msg_seq_cutoff before "
            f"{snapshot_at} for {src_tenant}/{src_session}"
        )
    return int(row[0])


def _normalize_snapshot_at(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = raw.replace("T", " ").replace("/", "-").strip()
    if "." in normalized:
        normalized = normalized.split(".", 1)[0]
    candidates = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ]
    for fmt in candidates:
        try:
            return datetime.strptime(normalized, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return normalized


def _normalize_source_session_key(value: str) -> str:
    session = str(value or "").strip()
    if session and ":" not in session:
        return f"main:{session}"
    return session


def _first_seed_value(spec: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = spec.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _first_seed_text(spec: dict[str, Any], *keys: str) -> str:
    value = _first_seed_value(spec, *keys)
    return str(value or "").strip()


def _seed_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


async def _seed_skin_profile(container, *, tenant_key: str, session_key: str, spec: Any) -> None:
    spec = spec if isinstance(spec, dict) else {}
    now = _now()
    created_at = str(spec.get("created_at") or now)
    image_url = str(spec.get("image_url") or "https://example.test/seed-face.jpg")
    skin_attribute = spec.get("skin_attribute") or {
        "stage": {"name": "年轻肌"},
        "toneType": {"name": "暖调二白"},
        "oilType": {"name": "混合性皮肤"},
    }
    signals = spec.get("signals") or [
        {
            "name": "PIH黑痘印",
            "code": "post_acne_hyperpigmentation",
            "regions": ["左面颊", "右面颊"],
            "careSuggestions": ["淡印", "防晒", "减少摩擦"],
        },
        {
            "name": "轻微泛红",
            "code": "redness",
            "regions": ["左面颊"],
            "careSuggestions": ["屏障修护", "减少刺激"],
        },
    ]
    advantages = spec.get("advantages") or ["轮廓紧致", "胶原充足"]

    async with container.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO nb_tenant_skin_profiles
                    (tenant_key, session_key, message_id, image_url, analysis_id,
                     skin_attribute_json, overall_state, advantages_json, signals_json,
                     sync_status, sync_reason, synced_to_user_doc_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_key,
                    session_key,
                    str(spec.get("message_id") or ""),
                    image_url,
                    str(spec.get("analysis_id") or hashlib.md5(image_url.encode("utf-8")).hexdigest()),
                    json.dumps(skin_attribute, ensure_ascii=False),
                    str(spec.get("overall_state") or "整体稳定，重点关注脸颊泛红与痘印。"),
                    json.dumps(advantages, ensure_ascii=False),
                    json.dumps(signals, ensure_ascii=False),
                    str(spec.get("sync_status") or "synced"),
                    str(spec.get("sync_reason") or "scenario_seed"),
                    now,
                    created_at,
                    now,
                ),
            )

    if not await container.doc_repo.get(tenant_key, "USER.md"):
        await container.doc_repo.set(
            tenant_key,
            "USER.md",
            "# 用户画像\n\n## Learned Skin Profile\n\n"
            "- 肤龄阶段：年轻肌\n"
            "- 肤色调：暖调二白\n"
            "- 肤质：混合性皮肤\n"
            "- 主要肤况：PIH黑痘印、轻微泛红\n"
            "- 问题分布：左面颊、右面颊\n"
            "- 皮肤总评：整体稳定，重点关注脸颊泛红与痘印。\n"
            "- 皮肤优势：轮廓紧致、胶原充足\n"
            "- 护理关注点：屏障修护、淡印、防晒\n"
            f"- 最近图片建档时间：{now[:10]}\n",
        )


async def _seed_memory_ledger(container, *, tenant_key: str, session_key: str, spec: Any) -> None:
    """Seed 一条 applied/pending 的 memory ledger，让 run_dream_now 不依赖对话压缩
    就有可调度的对象（dream 触发是基于 ledger，不是基于 memory 条目）。"""
    from simpleclaw.memory.ledger import MemoryLedgerRecord

    spec = spec if isinstance(spec, dict) else {}
    await container.memory_ledger_repo.create_ledger(
        MemoryLedgerRecord(
            tenant_key=tenant_key,
            session_key=session_key,
            source=str(spec.get("source") or "main"),
            status=str(spec.get("status") or "applied"),
            dream_status=str(spec.get("dream_status") or "pending"),
            trigger_type="context_compression",
        )
    )


async def _seed_skin_diary_result(container, *, tenant_key: str, spec: Any) -> None:
    spec = spec if isinstance(spec, dict) else {}
    now = _now()
    await container.skin_diary_result_repo.create_result(
        tenant_key=tenant_key,
        analyzed_at=str(spec.get("analyzed_at") or now),
        create_time=str(spec.get("create_time") or now),
        state=str(spec.get("state") or "stable"),
        summary=str(spec.get("summary") or "最近皮肤整体稳定，重点关注保湿和防晒。"),
        chips=list(spec.get("chips") or [{"label": "稳定", "tone": "neutral"}]),
        morning_steps=list(spec.get("morning_steps") or [{"title": "温和清洁", "detail": "避免过度清洁"}]),
        evening_steps=list(spec.get("evening_steps") or [{"title": "保湿修护", "detail": "面霜薄涂"}]),
        raw_output=dict(spec.get("raw_output") or {"source": "scenario_seed"}),
        creator="scenario_seed",
    )


async def _seed_image_job(container, *, tenant_key: str, session_key: str, spec: Any) -> None:
    if isinstance(spec, str):
        image_ref = spec
        status = "user_md_synced"
    elif isinstance(spec, dict):
        image_ref = str(spec.get("image_ref") or spec.get("url") or "https://example.test/seed-face.jpg")
        status = str(spec.get("status") or "user_md_synced")
    else:
        image_ref = "https://example.test/seed-face.jpg"
        status = "user_md_synced"
    await container.image_repo.create_job(
        tenant_key=tenant_key,
        session_key=session_key,
        image_ref=image_ref,
        focus="image_full",
        status=status,
    )


async def _seed_deep_report(container, *, tenant_key: str, session_key: str, spec: Any) -> None:
    spec = spec if isinstance(spec, dict) else {}
    now = _now()
    report_id = str(spec.get("report_id") or f"seed_{hashlib.md5((tenant_key + now).encode()).hexdigest()[:16]}")
    overview = {"summary": str(spec.get("overview") or "屏障状态略弱，需减少刺激并加强修护。")}
    decode = {"summary": str(spec.get("decode") or "主要关注泛红、干燥和痘印。")}
    secret = {"steps": [{"name": "温和修护", "detail": "洁面后保湿，白天防晒。"}]}
    track = {"items": [{"name": "泛红", "trend": "观察中"}]}
    async with container.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO nb_slow_model_reports
                    (report_id, user_id, session_id, status, model_name, model_version,
                     trace_id, overview_json, decode_json, secret_json, track_json,
                     raw_input_json, summary, creator, create_time, updater, update_time)
                VALUES (%s, %s, %s, %s, 'scenario', 'seed', %s, %s, %s, %s, %s, %s, %s,
                        'scenario_seed', %s, 'scenario_seed', %s)
                ON DUPLICATE KEY UPDATE
                    status=VALUES(status), overview_json=VALUES(overview_json),
                    decode_json=VALUES(decode_json), summary=VALUES(summary),
                    update_time=VALUES(update_time)
                """,
                (
                    report_id,
                    tenant_key,
                    session_key,
                    str(spec.get("status") or "done"),
                    report_id,
                    json.dumps(overview, ensure_ascii=False),
                    json.dumps(decode, ensure_ascii=False),
                    json.dumps(secret, ensure_ascii=False),
                    json.dumps(track, ensure_ascii=False),
                    json.dumps({"source": "scenario_seed"}, ensure_ascii=False),
                    str(spec.get("summary") or overview["summary"]),
                    now,
                    now,
                ),
            )


async def _seed_skincare_cabinet_product(container, *, tenant_key: str, spec: Any) -> None:
    spec = spec if isinstance(spec, dict) else {}
    brand = str(spec.get("brand") or "").strip()
    product_name = str(spec.get("product_name") or spec.get("productName") or "").strip()
    if not brand or not product_name:
        raise ValueError("seed.skincare_cabinet_products requires brand and product_name")
    product_id = await container.skincare_cabinet_repo.save_researched_product(
        user_id=tenant_key,
        brand=brand,
        product_name=product_name,
        usage_status=str(spec.get("usage_status") or "using").strip() or "using",
        image_url=str(spec.get("image_url") or spec.get("user_photo") or "").strip(),
        category=str(spec.get("category") or "面霜").strip(),
        core_efficacy=spec.get("core_efficacy") or ["保湿", "修护"],
        core_ingredients=spec.get("core_ingredients") or ["烟酰胺"],
        risk_ingredients=spec.get("risk_ingredients") or [],
        commercial_image=str(spec.get("commercial_image") or spec.get("image_url") or "").strip(),
        expiration_date=str(spec.get("expiration_date") or "").strip() or None,
        storage_conditions=str(spec.get("storage_conditions") or "常温避光保存").strip(),
        specifications=str(spec.get("specifications") or "30ml").strip(),
        creator="scenario_seed",
    )
    if bool(spec.get("in_cabinet", True)):
        await container.skincare_cabinet_repo.mark_in_cabinet(
            product_id=product_id,
            user_id=tenant_key,
            usage_status=str(spec.get("usage_status") or "using").strip() or "using",
        )


async def _run_turn(
    container,
    *,
    agent: str,
    tenant_key: str,
    session_key: str,
    turn: dict[str, Any],
    index: int,
    default_wait_s: float,
    default_prompt_surface: str = "app",
    default_device_id: Any = None,
    default_device_code: Any = None,
) -> dict[str, Any]:
    if turn.get("scenario_action"):
        return await _run_scenario_action_turn(
            container,
            tenant_key=tenant_key,
            session_key=session_key,
            turn=turn,
            index=index,
            default_wait_s=default_wait_s,
        )
    if turn.get("tool"):
        return await _run_tool_turn(
            container,
            agent=agent,
            tenant_key=tenant_key,
            session_key=session_key,
            turn=turn,
            index=index,
            default_wait_s=default_wait_s,
        )
    if agent == "main":
        result = await _run_main_turn(
            container,
            tenant_key=tenant_key,
            session_key=session_key,
            turn=turn,
            index=index,
            default_wait_s=default_wait_s,
            default_prompt_surface=default_prompt_surface,
            default_device_id=default_device_id,
            default_device_code=default_device_code,
        )
    else:
        result = await _run_subagent_turn(
            container,
            tenant_key=tenant_key,
            session_key=session_key,
            turn=turn,
            index=index,
            default_wait_s=default_wait_s,
        )
    return result


async def _run_scenario_action_turn(
    container,
    *,
    tenant_key: str,
    session_key: str,
    turn: dict[str, Any],
    index: int,
    default_wait_s: float,
) -> dict[str, Any]:
    spec = turn.get("scenario_action") or {}
    action = str(spec.get("name") if isinstance(spec, dict) else spec).strip()
    phase = turn.get("phase") or "setup"
    measure = bool(turn.get("measure", False))
    before = await _snapshot(container, tenant_key, session_key)
    capture = TurnCapture()
    error: str | None = None
    reply = ""
    extra_result: dict[str, Any] = {}

    try:
        action_result = await _apply_scenario_action(
            container,
            tenant_key=tenant_key,
            session_key=session_key,
            action=action,
            spec=spec if isinstance(spec, dict) else {},
        )
        if isinstance(action_result, dict):
            reply = str(action_result.get("reply") or f"scenario action applied: {action}")
            extra_result = dict(action_result)
        else:
            reply = str(action_result or f"scenario action applied: {action}")
    except Exception as exc:
        error = str(exc)
    finally:
        capture.mark_first_token()
        capture.mark_done()

    wait_s = float(turn.get("wait_side_effects_s", default_wait_s) or 0)
    if wait_s > 0:
        await asyncio.sleep(wait_s)
    after = await _snapshot(container, tenant_key, session_key)
    after = await _wait_until_checks(
        container,
        tenant_key=tenant_key,
        session_key=session_key,
        before=before,
        after=after,
        turn=turn,
        reply=reply,
        error=error,
        capture=capture,
    )

    return _build_turn_result(
        index=index,
        message=str(turn.get("user") or f"[scenario_action] {action}"),
        phase=phase,
        measure=measure,
        reply=reply,
        error=error,
        capture=capture,
        before=before,
        after=after,
        extra=extra_result,
    )


def _dream_force_allowed(tenant_key: str, *, bypass_cooldown: bool) -> bool:
    """仅 test_* tenant 且显式 bypass 时，允许 force admission 跳过冷却。"""
    return bool(bypass_cooldown) and str(tenant_key or "").startswith("test_")


async def _apply_scenario_action(
    container,
    *,
    tenant_key: str,
    session_key: str,
    action: str,
    spec: dict[str, Any],
) -> dict[str, Any] | str | None:
    if action == "start_session_event_listener":
        target_session_key = _resolve_action_session_key(
            spec.get("session_key"),
            tenant_key=tenant_key,
            default=session_key,
        )
        await _start_session_event_listener(
            container,
            tenant_key=tenant_key,
            session_key=target_session_key,
        )
        return {
            "reply": f"started session event listener for {target_session_key}",
            "session_events": [],
        }

    if action == "wait_for_session_events":
        timeout_s = float(spec.get("timeout_s") or 180)
        max_events = int(spec.get("max_events") or 32)
        expect_source = str(spec.get("source") or "").strip()
        expect_activation_kind = str(spec.get("activation_kind") or "").strip()
        target_session_key = _resolve_action_session_key(
            spec.get("session_key"),
            tenant_key=tenant_key,
            default=session_key,
        )
        use_existing_listener = bool(spec.get("use_existing_listener", True))
        if use_existing_listener and _get_session_event_listener(container, tenant_key=tenant_key, session_key=target_session_key):
            events = await _wait_for_buffered_session_events(
                container,
                tenant_key=tenant_key,
                session_key=target_session_key,
                timeout_s=timeout_s,
                max_events=max_events,
                expect_source=expect_source,
                expect_activation_kind=expect_activation_kind,
            )
        else:
            events = await _wait_for_session_events(
                container,
                tenant_key=tenant_key,
                session_key=target_session_key,
                timeout_s=timeout_s,
                max_events=max_events,
                expect_source=expect_source,
                expect_activation_kind=expect_activation_kind,
            )
        texts: list[str] = []
        for event in events:
            event_type = str(event.get("type") or "")
            if event_type == "chunk":
                text = str(((event.get("data") or {}).get("text")) or event.get("text") or "")
                if text:
                    texts.append(text)
        reply = "".join(texts).strip() or f"captured {len(events)} session events"
        return {
            "reply": reply,
            "session_events": events,
        }

    if action == "mark_latest_runtime_task":
        task_type = str(spec.get("task_type") or "").strip()
        status = str(spec.get("status") or "succeeded").strip()
        if not task_type:
            raise ValueError("scenario_action.mark_latest_runtime_task requires task_type")
        task = await container.runtime_task_repo.find_latest_task_for(
            tenant_key=tenant_key,
            task_type=task_type,
        )
        if not task:
            return
        task_id = str(task.get("task_id") or "")
        if status == "succeeded":
            await container.runtime_task_repo.mark_succeeded(
                task_id,
                summary=str(spec.get("summary") or "scenario action marked succeeded"),
            )
            return
        if status == "failed":
            await container.runtime_task_repo.mark_failed(
                task_id,
                error=str(spec.get("error") or "scenario action marked failed"),
            )
            return
        if status == "wait_external":
            await container.runtime_task_repo.mark_wait_external(
                task_id,
                external_job_id=str(task.get("external_job_id") or ""),
                summary=str(spec.get("summary") or "scenario action marked wait_external"),
            )
            return
        raise ValueError(f"unsupported runtime task status for scenario action: {status}")

    if action == "age_latest_image_job":
        days = int(spec.get("days") or 1)
        if days <= 0:
            raise ValueError("scenario_action.age_latest_image_job days must be positive")
        await _age_latest_image_job(container, tenant_key=tenant_key, days=days)
        return

    if action == "run_dream_now":
        return await _run_dream_now(
            container,
            tenant_key=tenant_key,
            bypass_cooldown=bool(spec.get("bypass_cooldown", True)),
            timeout_s=float(spec.get("timeout_s") or 60),
        )

    if action in {"wait_for_side_effects", "wait", "noop"}:
        return

    if action == "advance_runtime_time":
        seconds = _scenario_time_delta_seconds(spec)
        if seconds <= 0:
            raise ValueError("scenario_action.advance_runtime_time requires a positive time delta")
        result = await _advance_runtime_time(container, tenant_key=tenant_key, seconds=seconds)
        hours = seconds / 3600
        return {
            "reply": f"advanced runtime time by {hours:g}h",
            **result,
        }

    if action == "complete_latest_cabinet_research":
        task = await container.runtime_task_repo.find_latest_task_for(
            tenant_key=tenant_key,
            task_type="cabinet_product_research",
        )
        if not task:
            return

        payload = dict(task.get("payload") or {})
        brand = str(spec.get("brand") or payload.get("brand") or payload.get("productName") or "").strip()
        product_name = str(spec.get("product_name") or payload.get("productName") or "").strip()
        usage_status = str(spec.get("usage_status") or payload.get("usage_status") or "using").strip() or "using"
        image_url = str(spec.get("image_url") or payload.get("imageUrl") or "").strip()
        if not brand or not product_name:
            raise ValueError("scenario_action.complete_latest_cabinet_research requires brand and product_name")

        product_id = await container.skincare_cabinet_repo.save_researched_product(
            user_id=tenant_key,
            brand=brand,
            product_name=product_name,
            usage_status=usage_status,
            image_url=image_url,
            category=str(spec.get("category") or "精华/面霜").strip(),
            core_efficacy=spec.get("core_efficacy") or ["提亮", "修护"],
            core_ingredients=spec.get("core_ingredients") or ["烟酰胺", "光甘草定"],
            risk_ingredients=spec.get("risk_ingredients") or [],
            commercial_image=str(spec.get("commercial_image") or image_url).strip(),
            expiration_date=str(spec.get("expiration_date") or "").strip() or None,
            storage_conditions=str(spec.get("storage_conditions") or "常温避光保存").strip(),
            specifications=str(spec.get("specifications") or "30ml").strip(),
            creator="scenario_action.complete_latest_cabinet_research",
        )
        await container.runtime_task_repo.mark_succeeded(
            str(task.get("task_id") or ""),
            summary=str(spec.get("summary") or "scenario action completed cabinet product research"),
            business_ref_type="skincare_cabinet_product",
            business_ref_id=str(product_id),
            output_json={
                "product_id": product_id,
                "brand": brand,
                "product_name": product_name,
                "in_cabinet": 0,
                "usage_status": usage_status,
                "source": "scenario_action.complete_latest_cabinet_research",
            },
        )
        await dispatch_obligations_for_dependency(
            obligation_repo=container.obligation_repo,
            runtime=container.runtime,
            tenant_key=tenant_key,
            dependency_type=DEPENDENCY_CABINET_PRODUCT_RESEARCH_SUCCEEDED,
            source_session_key=str(task.get("session_key") or session_key or ""),
            source_task_id=str(task.get("task_id") or ""),
            dependency_business_ref_type="skincare_cabinet_product",
            dependency_business_ref_id=str(product_id),
            document_repo=container.doc_repo,
        )
        return

    if action == "set_user_md":
        content = str(spec.get("content") or "").strip()
        if not content:
            raise ValueError("scenario_action.set_user_md requires non-empty content")
        await container.doc_repo.set(tenant_key, "USER.md", content)
        return

    if action == "mock_latest_image_analysis_profile_ready":
        task = await container.runtime_task_repo.find_latest_task_for(
            tenant_key=tenant_key,
            task_type="image_analysis",
        )
        payload = dict((task or {}).get("payload") or {})
        image_ref = str(spec.get("image_url") or payload.get("image") or payload.get("image_ref") or "").strip()
        image_id = str(spec.get("analysis_id") or payload.get("image_id") or "").strip()
        message_id = str(spec.get("message_id") or payload.get("message_id") or "").strip()
        latest_job = await container.image_repo.find_latest_job(tenant_key)
        if not image_ref:
            image_ref = str((latest_job or {}).get("image_ref") or "").strip()
        if not image_id:
            image_id = str((latest_job or {}).get("image_id") or "").strip()
        if not message_id:
            message_id = str((latest_job or {}).get("message_id") or "").strip()
        if not image_ref:
            image_ref = "https://example.test/seed-face.jpg"

        job = await container.image_repo.create_job(
            tenant_key=tenant_key,
            session_key=session_key,
            image_ref=image_ref,
            message_id=message_id or None,
            focus="image_full",
            status="uploaded",
        )
        envelope = build_image_analysis_envelope(
            tenant_key=tenant_key,
            session_key=session_key,
            origin_session_key=session_key,
            image_ref=image_ref,
            job_id=str(job.get("job_id") or ""),
            image_id=str(image_id or job.get("image_id") or ""),
            message_id=message_id or str(job.get("message_id") or ""),
            query=str(spec.get("query") or payload.get("query") or ""),
            source="scenario_mock",
        )
        queue_id = str(spec.get("queue_id") or "scenario-mock-image-analysis")
        await container.runtime_task_repo.record_queued(
            envelope,
            queue_message_id=queue_id,
            tool_name="analyze_image",
            summary=str(spec.get("queued_summary") or "scenario action queued mocked image analysis"),
        )
        await container.image_repo.mark_queued(
            str(job.get("job_id") or ""),
            task_id=envelope.task_id,
            queue_id=queue_id,
            payload=envelope.payload,
        )
        await container.runtime_task_repo.mark_wait_external(
            envelope.task_id,
            external_job_id=str(spec.get("external_job_id") or "mock-image-analysis"),
            summary=str(spec.get("summary") or "scenario action mocked image analysis wait_external"),
        )
        await container.image_repo.mark_wait_external(
            str(job.get("job_id") or ""),
            external_job_id=str(spec.get("external_job_id") or "mock-image-analysis"),
            response={"source": "scenario_action.mock_latest_image_analysis_profile_ready"},
        )
        await _seed_skin_profile(
            container,
            tenant_key=tenant_key,
            session_key=session_key,
            spec={
                "image_url": image_ref or "https://example.test/seed-face.jpg",
                "analysis_id": image_id or str(job.get("image_id") or "") or hashlib.md5((image_ref or tenant_key).encode("utf-8")).hexdigest(),
                "message_id": message_id or str(job.get("message_id") or ""),
                "sync_status": str(spec.get("sync_status") or "pending"),
                "sync_reason": str(spec.get("sync_reason") or ""),
                "skin_attribute": spec.get("skin_attribute"),
                "signals": spec.get("signals"),
                "advantages": spec.get("advantages"),
                "overall_state": str(spec.get("overall_state") or "整体稳定，重点关注黑眼圈与痘印。"),
            },
        )
        return

    raise ValueError(f"unsupported scenario_action: {action}")


async def _run_dream_now(
    container,
    *,
    tenant_key: str,
    bypass_cooldown: bool,
    timeout_s: float,
) -> dict[str, Any]:
    """场景内同步触发 dream：调度 applied ledger 的 candidate，再轮询 job 到完成。"""
    force = _dream_force_allowed(tenant_key, bypass_cooldown=bypass_cooldown)
    if bypass_cooldown and not force:
        logger.warning("run_dream_now: bypass_cooldown ignored for non-test tenant {}", tenant_key)

    # dream 前 skin 条目数（用于"合并前后条目数"对比，看 severity 有没有收敛）
    skin_before = sum(1 for e in await _memory_entries(container, tenant_key) if e.get("is_skin"))

    ledgers = await container.memory_ledger_repo.list_dream_pending(
        tenant_key=tenant_key, limit=10
    )
    job_ids: list[str] = []
    for ledger in ledgers:
        if ledger.status != "applied":
            continue
        signal = memory_ledger_applied_signal(ledger)
        candidate = DreamCandidate.from_signal(signal, trigger="memory_threshold")
        ctx = DreamAdmissionContext(force=True) if force else None
        result = await container.dream_scheduler.schedule(candidate, context=ctx)
        if result.admitted and result.job is not None:
            job_ids.append(result.job.job_id)
            await container.memory_ledger_repo.update_ledger(
                ledger.ledger_id,
                dream_status="candidate",
                metadata={"dream_job_id": result.job.job_id},
            )

    # 轮询 job 到 completed / failed / 超时
    deadline = time.monotonic() + timeout_s
    final_status: dict[str, str] = {}
    pending = set(job_ids)
    while pending and time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        for job_id in list(pending):
            job = await container.dream_repo.get_job(job_id)
            status = str(getattr(job, "status", "") or "")
            if status in {"completed", "failed", "skipped"}:
                final_status[job_id] = status
                pending.discard(job_id)

    for job_id in pending:
        final_status[job_id] = "timeout"

    # dream 后 skin 条目数；合并成功应当减少（多条冗余 → 收敛为更少的趋势条目）
    skin_after = sum(1 for e in await _memory_entries(container, tenant_key) if e.get("is_skin"))

    return {
        "reply": (
            f"run_dream_now scheduled={len(job_ids)} statuses={final_status} "
            f"skin_before={skin_before} skin_after={skin_after} delta={skin_after - skin_before}"
        ),
        "dream_job_ids": job_ids,
        "dream_status": final_status,
        "skin_before": skin_before,
        "skin_after": skin_after,
        "skin_merge_delta": skin_after - skin_before,
    }


def _resolve_action_session_key(raw: Any, *, tenant_key: str, default: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return default
    return text.replace("{tenant_key}", tenant_key)


async def _start_scenario_session_listeners(
    container,
    *,
    tenant_key: str,
    default_session_key: str,
    scenario: dict[str, Any],
) -> None:
    session_cfg = scenario.get("session") or {}
    subscriptions = session_cfg.get("subscriptions") or []
    if isinstance(subscriptions, (str, dict)):
        subscriptions = [subscriptions]

    for item in subscriptions:
        if isinstance(item, dict):
            raw_session_key = item.get("session_key") or item.get("session")
        else:
            raw_session_key = item
        target_session_key = _resolve_action_session_key(
            raw_session_key,
            tenant_key=tenant_key,
            default=default_session_key,
        )
        await _start_session_event_listener(
            container,
            tenant_key=tenant_key,
            session_key=target_session_key,
        )


def _event_listener_key(*, tenant_key: str, session_key: str) -> str:
    return f"{tenant_key}:{session_key}"


def _listener_registry(container) -> dict[str, dict[str, Any]]:
    registry = getattr(container, "_scenario_event_listeners", None)
    if registry is None:
        registry = {}
        setattr(container, "_scenario_event_listeners", registry)
    return registry


def _get_session_event_listener(container, *, tenant_key: str, session_key: str) -> dict[str, Any] | None:
    return _listener_registry(container).get(_event_listener_key(tenant_key=tenant_key, session_key=session_key))


async def _start_session_event_listener(
    container,
    *,
    tenant_key: str,
    session_key: str,
) -> None:
    registry = _listener_registry(container)
    key = _event_listener_key(tenant_key=tenant_key, session_key=session_key)
    existing = registry.get(key)
    if existing is not None:
        return

    state: dict[str, Any] = {
        "tenant_key": tenant_key,
        "session_key": session_key,
        "events": [],
        "closed": False,
        "new_event": asyncio.Event(),
    }

    async def _pump() -> None:
        iterator = container.event_hub.subscribe(tenant_key, session_key)
        try:
            async for event in iterator:
                if isinstance(event, dict):
                    state["events"].append(event)
                    state["new_event"].set()
        finally:
            state["closed"] = True
            state["new_event"].set()
            await iterator.aclose()

    state["task"] = asyncio.create_task(_pump())
    registry[key] = state


async def _wait_for_buffered_session_events(
    container,
    *,
    tenant_key: str,
    session_key: str,
    timeout_s: float,
    max_events: int,
    expect_source: str,
    expect_activation_kind: str,
) -> list[dict[str, Any]]:
    state = _get_session_event_listener(container, tenant_key=tenant_key, session_key=session_key)
    if state is None:
        return await _wait_for_session_events(
            container,
            tenant_key=tenant_key,
            session_key=session_key,
            timeout_s=timeout_s,
            max_events=max_events,
            expect_source=expect_source,
            expect_activation_kind=expect_activation_kind,
        )

    started_at = time.monotonic()
    cursor = 0
    events: list[dict[str, Any]] = []

    while len(events) < max_events:
        buffered = state.get("events") or []
        while cursor < len(buffered) and len(events) < max_events:
            event = buffered[cursor]
            cursor += 1
            if not isinstance(event, dict):
                continue
            if expect_source:
                data = event.get("data") or {}
                source = str(data.get("source") or event.get("source") or "")
                if source != expect_source:
                    continue
            if expect_activation_kind:
                data = event.get("data") or {}
                kind = str(data.get("activation_kind") or event.get("activation_kind") or "")
                if kind != expect_activation_kind:
                    continue
            events.append(event)
            if str(event.get("type") or "") in {"done", "error"}:
                return events

        remaining = timeout_s - (time.monotonic() - started_at)
        if remaining <= 0 or state.get("closed"):
            break
        trigger = state["new_event"]
        trigger.clear()
        try:
            await asyncio.wait_for(trigger.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            break

    return events


async def _wait_for_session_events(
    container,
    *,
    tenant_key: str,
    session_key: str,
    timeout_s: float,
    max_events: int,
    expect_source: str,
    expect_activation_kind: str,
) -> list[dict[str, Any]]:
    started_at = time.monotonic()
    events: list[dict[str, Any]] = []
    iterator = container.event_hub.subscribe(tenant_key, session_key)
    try:
        while len(events) < max_events:
            remaining = timeout_s - (time.monotonic() - started_at)
            if remaining <= 0:
                break
            try:
                event = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
            except (asyncio.TimeoutError, StopAsyncIteration):
                break
            if not isinstance(event, dict):
                continue
            if expect_source:
                data = event.get("data") or {}
                source = str(data.get("source") or event.get("source") or "")
                if source != expect_source:
                    continue
            if expect_activation_kind:
                data = event.get("data") or {}
                kind = str(data.get("activation_kind") or event.get("activation_kind") or "")
                if kind != expect_activation_kind:
                    continue
            events.append(event)
            if str(event.get("type") or "") in {"done", "error"}:
                break
    finally:
        await iterator.aclose()
    return events


async def _age_latest_image_job(container, *, tenant_key: str, days: int) -> None:
    async with container.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT job_id, created_at, updated_at, started_at, completed_at
                  FROM nb_image_analysis_jobs
                 WHERE tenant_key = %s
                   AND status = 'succeeded'
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                (tenant_key,),
            )
            row = await cur.fetchone()
    if row is None:
        return
    job_id = str(row[0] or "")
    shifted = {
        "created_at": _shift_mysql_time(row[1], days=days),
        "updated_at": _shift_mysql_time(row[2], days=days),
        "started_at": _shift_mysql_time(row[3], days=days),
        "completed_at": _shift_mysql_time(row[4], days=days),
    }
    async with container.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE nb_image_analysis_jobs
                   SET created_at = %s,
                       updated_at = %s,
                       started_at = %s,
                       completed_at = %s
                 WHERE tenant_key = %s
                   AND job_id = %s
                """,
                (
                    shifted["created_at"],
                    shifted["updated_at"],
                    shifted["started_at"],
                    shifted["completed_at"],
                    tenant_key,
                    job_id,
                ),
            )


def _scenario_time_delta_seconds(spec: dict[str, Any]) -> int:
    seconds = int(spec.get("seconds") or 0)
    seconds += int(float(spec.get("minutes") or 0) * 60)
    seconds += int(float(spec.get("hours") or 0) * 3600)
    seconds += int(float(spec.get("days") or 0) * 86400)
    return seconds


async def _advance_runtime_time(container, *, tenant_key: str, seconds: int) -> dict[str, Any]:
    """Age tenant-scoped records so later turns observe elapsed time without sleeping."""
    table_specs: list[tuple[str, str, tuple[str, ...]]] = [
        ("nb_sessions", "tenant_key", ("created_at", "updated_at")),
        ("nb_session_messages", "tenant_key", ("created_at",)),
        ("nb_tenant_documents", "tenant_key", ("created_at", "updated_at")),
        ("nb_tenant_document_versions", "tenant_key", ("created_at",)),
        ("nb_image_analysis_jobs", "tenant_key", ("created_at", "updated_at", "started_at", "completed_at")),
        ("nb_runtime_tasks", "tenant_key", ("created_at", "updated_at", "completed_at")),
        ("nb_runtime_completion_events", "tenant_key", ("created_at", "updated_at", "consumed_at")),
        ("nb_agent_tool_invocations", "tenant_key", ("created_at", "updated_at", "completed_at")),
        ("nb_agent_obligations", "tenant_key", ("created_at", "updated_at")),
        ("nb_tenant_skin_profiles", "tenant_key", ("synced_to_user_doc_at", "created_at", "updated_at")),
        ("nb_tenant_profile_block_meta", "tenant_key", ("last_synced_at", "created_at", "updated_at")),
        ("nb_tenant_memory_events", "tenant_key", ("created_at",)),
        ("nb_memory_entries", "tenant_key", ("last_referenced_at", "created_at", "updated_at")),
        ("nb_skin_diary_results", "tenant_key", ("analyzed_at", "create_time", "update_time")),
        ("nb_skin_diary_sessions", "tenant_key", ("last_active_at", "created_at", "updated_at")),
        ("nb_slow_model_reports", "user_id", ("create_time", "update_time")),
        ("nb_deep_analysis_reports", "user_id", ("create_time", "update_time")),
        ("nb_agent_field_reports", "user_id", ("create_time", "update_time")),
    ]
    updated: dict[str, int] = {}
    async with container.db.acquire() as conn:
        async with conn.cursor() as cur:
            for table, tenant_column, columns in table_specs:
                assignments = ", ".join(
                    f"{column} = CASE WHEN {column} IS NULL THEN NULL ELSE DATE_SUB({column}, INTERVAL %s SECOND) END"
                    for column in columns
                )
                params: list[Any] = [seconds for _ in columns]
                params.append(tenant_key)
                await cur.execute(
                    f"""
                    UPDATE {table}
                       SET {assignments}
                     WHERE {tenant_column} = %s
                    """,
                    tuple(params),
                )
                updated[table] = int(cur.rowcount or 0)
    return {
        "advanced_seconds": seconds,
        "advanced_tables": updated,
    }


def _shift_mysql_time(value: Any, *, days: int) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return (value - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return (datetime.strptime(text, fmt) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    try:
        return (datetime.fromisoformat(text) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text


async def _run_tool_turn(
    container,
    *,
    agent: str,
    tenant_key: str,
    session_key: str,
    turn: dict[str, Any],
    index: int,
    default_wait_s: float,
) -> dict[str, Any]:
    spec = turn.get("tool") or {}
    if isinstance(spec, str):
        tool_name = spec
        arguments: dict[str, Any] = {}
    else:
        tool_name = str(spec.get("name") or "")
        arguments = dict(spec.get("arguments") or {})
    message = str(turn.get("user") or arguments.get("query") or f"[tool] {tool_name}")
    media = list(turn.get("media") or [])
    phase = turn.get("phase") or "tool"
    measure = bool(turn.get("measure", phase == "measured"))
    before = await _snapshot(container, tenant_key, session_key)

    if media:
        await _record_uploaded_images(
            container,
            tenant_key=tenant_key,
            session_key=session_key,
            media=media,
            message_id=None,
            query=message,
        )

    registry = _build_tool_registry_for_agent(
        container,
        agent=agent,
        tenant_key=tenant_key,
        session_key=session_key,
        query=message,
        media=media,
    )

    capture = TurnCapture()
    restore = wrap_tool_registry(registry, capture)
    error: str | None = None
    try:
        result = await registry.execute(
            ToolCall(
                id=str((spec.get("id") if isinstance(spec, dict) else "") or f"scenario_{index}"),
                name=tool_name,
                arguments=arguments,
            )
        )
        capture.mark_first_token()
        capture.mark_done()
        reply = str(getattr(result, "content", "") or "")
    except Exception as exc:
        error = str(exc)
        capture.mark_done()
        reply = ""
    finally:
        restore()

    wait_s = float(turn.get("wait_side_effects_s", default_wait_s) or 0)
    if wait_s > 0:
        await asyncio.sleep(wait_s)
    after = await _snapshot(container, tenant_key, session_key)
    after = await _wait_until_checks(
        container,
        tenant_key=tenant_key,
        session_key=session_key,
        before=before,
        after=after,
        turn=turn,
        reply=reply,
        error=error,
        capture=capture,
    )

    return _build_turn_result(
        index=index,
        message=message,
        media=media,
        phase=phase,
        measure=measure,
        reply=reply,
        error=error,
        capture=capture,
        before=before,
        after=after,
    )


def _build_tool_registry_for_agent(
    container,
    *,
    agent: str,
    tenant_key: str,
    session_key: str,
    query: str,
    media: list[str],
):
    if agent == "main":
        registry = container.main_agent.make_tool_registry(
            tenant_key,
            stage="explore",
        )
    else:
        subagent = container.subagent_store.find_subagent(session_key)
        if subagent is None:
            raise ValueError(f"No subagent registered for session_key={session_key!r}")
        registry = subagent.make_tool_registry(tenant_key)

    registry.set_runtime_services(container.runtime)
    for tool in registry.tools:
        if hasattr(tool, "set_context"):
            tool.set_context(
                tenant_key=tenant_key,
                session_key=session_key,
                origin_session_key=f"main:{tenant_key}",
                query=query,
                media=media,
                message_id=None,
            )
    return registry


async def _run_main_turn(
    container,
    *,
    tenant_key: str,
    session_key: str,
    turn: dict[str, Any],
    index: int,
    default_wait_s: float,
    default_prompt_surface: str = "app",
    default_device_id: Any = None,
    default_device_code: Any = None,
) -> dict[str, Any]:
    turn_started_ms = int(time.time() * 1000)
    message = str(turn.get("user") or "")
    media = list(turn.get("media") or [])
    prompt_surface = str(turn.get("prompt_surface") or default_prompt_surface or "app").strip().lower() or "app"
    device_id = turn.get("device_id") or turn.get("deviceId") or default_device_id
    device_code = turn.get("device_code") or turn.get("deviceCode") or default_device_code
    phase = turn.get("phase") or "measured"
    measure = bool(turn.get("measure", phase == "measured"))
    logger.info(
        "scenario.turn.start idx={} tenant={} session={} msg_len={} media_n={}",
        index, tenant_key, session_key, len(message), len(media),
    )
    before = await _snapshot(container, tenant_key, session_key)
    logger.info("scenario.turn.snapshot_before.done idx={}", index)

    if media:
        await _record_uploaded_images(
            container,
            tenant_key=tenant_key,
            session_key=session_key,
            media=media,
            message_id=None,
            query=message,
        )
        logger.info("scenario.turn.media_recorded idx={}", index)

    capabilities = capabilities_from_device_context(
        device_id=device_id,
        device_code=device_code,
        prompt_surface=prompt_surface,
    )
    loop = await container.sessions.get_or_create(
        session_key,
        tenant_key,
        capabilities=capabilities,
    )
    await container.sessions.maybe_compress(session_key, tenant_key)
    logger.info("scenario.turn.session_ready idx={} history_n={}", index, len(loop.messages))

    capture = TurnCapture()
    restore = wrap_all_tool_registries(capture)
    prompt_captures: list[dict[str, Any]] = []
    attention_captures: list[dict[str, Any]] = []
    prompt_capture_chars = int(turn.get("capture_prompt_chars") or (50000 if index == 1 else 12000))
    main_reply_parts: list[str] = []
    first_token_parts: list[str] = []
    error: str | None = None
    first_token_status = ""
    first_token_detail = ""
    first_token_prompt_capture: dict[str, Any] | None = None
    opener_input = build_first_token_user_message(message, media)
    if getattr(container, "first_token_agent", None) is not None and opener_input.strip():
        logger.info("scenario.turn.first_token_prompt_capture.start idx={}", index)
        first_token_prompt_capture = await _capture_first_token_prompt(
            container.first_token_agent,
            tenant_key=tenant_key,
            session_key=session_key,
            user_message=opener_input,
            history=loop.messages,
            consolidated_from=loop.consolidated_from,
            history_offset=getattr(loop, "history_offset", None),
            prompt_surface=prompt_surface,
        )
        logger.info("scenario.turn.first_token_prompt_capture.done idx={}", index)

    capture_prompt = bool(turn.get("capture_prompt", True))

    def _on_prompt_messages(messages: list[dict[str, Any]]) -> None:
        if not capture_prompt:
            return
        prompt_captures.append(_summarize_prompt_messages(
            messages,
            call_no=len(prompt_captures) + 1,
            max_chars=max(200, prompt_capture_chars),
        ))

    def _on_attention_packets(packets: list[AttentionPacket]) -> None:
        if not capture_prompt:
            return
        attention_captures.append({
            "call": len(attention_captures) + 1,
            "packets": _summarize_attention_packets(packets),
        })

    def _encode_event(kind: str, **payload: Any) -> str:
        return json.dumps({"kind": kind, **payload}, ensure_ascii=False)

    def _on_main_text(token: str) -> str:
        return _encode_event("main_text", text=token)

    def _on_first_token_text(token: str) -> str:
        return _encode_event("first_token_text", text=token)

    def _on_first_token_status(status: str, **payload: Any) -> str:
        return _encode_event("first_token_status", status=status, **payload)

    def _on_done() -> str:
        return _encode_event("done")

    def _on_error(msg: str) -> str:
        return _encode_event("error", error=msg)

    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=128)
    ingress = container.main_session_ingress
    if ingress is None:
        raise RuntimeError("main_session_ingress is not initialized")

    should_cancel_ingress = True
    try:
        logger.info("scenario.turn.ingress_submit.start idx={}", index)
        ingress_id = await ingress.submit_user_message(
            session_key=session_key,
            tenant_key=tenant_key,
            message=message,
            queue=queue,
            on_text=_on_main_text,
            on_done=_on_done,
            on_error=_on_error,
            on_first_token_text=_on_first_token_text,
            on_first_token_status=_on_first_token_status,
            media=media,
            message_id=None,
            device_id=device_id,
            device_code=device_code,
            prompt_surface=prompt_surface,
            origin_session_key=f"main:{tenant_key}",
            on_prompt_messages=_on_prompt_messages if capture_prompt else None,
            on_attention_packets=_on_attention_packets if capture_prompt else None,
        )
        logger.info("scenario.turn.ingress_submit.done idx={} ingress_id={}", index, ingress_id)
        while True:
            raw = await queue.get()
            if raw is None:
                break
            try:
                event = json.loads(raw)
            except Exception:
                continue
            kind = str(event.get("kind") or "")
            if kind == "first_token_text":
                capture.mark_first_token()
                first_token_parts.append(str(event.get("text") or ""))
            elif kind == "main_text":
                capture.mark_first_token()
                main_reply_parts.append(str(event.get("text") or ""))
            elif kind == "first_token_status":
                first_token_status = str(event.get("status") or "")
                if event.get("detail"):
                    first_token_detail = str(event.get("detail") or "")
            elif kind == "done":
                capture.mark_done()
                continue
            elif kind == "error":
                error = str(event.get("error") or "")
                capture.mark_done()
                break
        if error is None:
            should_cancel_ingress = False
    finally:
        if should_cancel_ingress and "ingress_id" in locals():
            await ingress.cancel(ingress_id)
        restore()

    first_token_reply = "".join(first_token_parts).strip()
    main_reply = "".join(main_reply_parts)
    reply = join_first_token_reply(first_token_reply, main_reply)
    wait_s = float(turn.get("wait_side_effects_s", default_wait_s) or 0)
    if wait_s > 0:
        await asyncio.sleep(wait_s)
    after = await _snapshot(container, tenant_key, session_key)
    after = await _wait_until_checks(
        container,
        tenant_key=tenant_key,
        session_key=session_key,
        before=before,
        after=after,
        turn=turn,
        reply=reply,
        error=error,
        capture=capture,
    )
    doc_delta = _doc_delta(before, after)
    runtime_tasks_created = _runtime_tasks_created(before, after)
    image_jobs_created = _image_jobs_created(before, after)
    cron_jobs_created = _cron_jobs_created(before, after)
    session_delta = _session_delta(before, after)
    subagent_session_delta = _subagent_session_delta(before, after)

    return {
        "turn": index,
        "user": message,
        "media": media,
        "prompt_surface": prompt_surface,
        "device_id": device_id,
        "device_code": device_code,
        "phase": phase,
        "measure": measure,
        "reply": reply,
        "first_token_reply": first_token_reply,
        "first_token_status": first_token_status,
        "first_token_detail": first_token_detail,
        "first_token_prompt_capture": first_token_prompt_capture,
        "main_reply": main_reply,
        "error": error,
        "ttft_ms": capture.ttft_ms,
        "total_ms": capture.total_ms,
        "tools_called": capture.tools_called,
        "tools": [item.__dict__ for item in capture.tools],
        "prompt_captures": prompt_captures,
        "attention_captures": attention_captures,
        "runtime_tasks_created": runtime_tasks_created,
        "image_jobs_created": image_jobs_created,
        "cron_jobs_created": cron_jobs_created,
        "docs_before": before["docs"],
        "docs_after": after["docs"],
        "doc_delta": doc_delta,
        "topic_state_before": before.get("topic_state"),
        "topic_state_after": after.get("topic_state"),
        "runtime_tasks_after": after["runtime_tasks"],
        "image_jobs_before": before["image_jobs"],
        "image_jobs_after": after["image_jobs"],
        "cron_jobs_before": before["cron_jobs"],
        "cron_jobs_after": after["cron_jobs"],
        "session_before": before["session"],
        "session_after": after["session"],
        "session_delta": session_delta,
        "subagent_sessions_before": before["subagent_sessions"],
        "subagent_sessions_after": after["subagent_sessions"],
        "subagent_session_delta": subagent_session_delta,
        "skin_diary_results_created": _skin_diary_results_created(before, after),
        "skin_diary_results_before": before["skin_diary_results"],
        "skin_diary_results_after": after["skin_diary_results"],
    }


def _build_turn_result(
    *,
    index: int,
    message: str,
    phase: str,
    measure: bool,
    reply: str,
    error: str | None,
    capture: TurnCapture,
    before: dict[str, Any],
    after: dict[str, Any],
    extra: dict[str, Any] | None = None,
    media: list[str] | None = None,
) -> dict[str, Any]:
    result = {
        "turn": index,
        "user": message,
        "media": list(media or []),
        "phase": phase,
        "measure": measure,
        "reply": reply,
        "first_token_reply": "",
        "first_token_status": "",
        "first_token_detail": "",
        "main_reply": reply,
        "error": error,
        "ttft_ms": capture.ttft_ms,
        "total_ms": capture.total_ms,
        "tools_called": capture.tools_called,
        "tools": [item.__dict__ for item in capture.tools],
        "prompt_captures": [],
        "attention_captures": [],
        "runtime_tasks_created": _runtime_tasks_created(before, after),
        "image_jobs_created": _image_jobs_created(before, after),
        "cron_jobs_created": _cron_jobs_created(before, after),
        "docs_before": before["docs"],
        "docs_after": after["docs"],
        "doc_delta": _doc_delta(before, after),
        "topic_state_before": before.get("topic_state"),
        "topic_state_after": after.get("topic_state"),
        "runtime_tasks_after": after["runtime_tasks"],
        "image_jobs_before": before["image_jobs"],
        "image_jobs_after": after["image_jobs"],
        "cron_jobs_before": before["cron_jobs"],
        "cron_jobs_after": after["cron_jobs"],
        "session_before": before["session"],
        "session_after": after["session"],
        "session_delta": _session_delta(before, after),
        "subagent_sessions_before": before["subagent_sessions"],
        "subagent_sessions_after": after["subagent_sessions"],
        "subagent_session_delta": _subagent_session_delta(before, after),
        "skin_diary_results_created": _skin_diary_results_created(before, after),
        "skin_diary_results_before": before["skin_diary_results"],
        "skin_diary_results_after": after["skin_diary_results"],
    }
    result["memory_entries"] = (after or {}).get("memory_entries") or []
    result["memory_ledgers"] = (after or {}).get("memory_ledgers") or []
    result["dream_artifacts"] = (after or {}).get("dream_artifacts") or []
    if extra:
        result.update(extra)
    return result


async def _run_subagent_turn(
    container,
    *,
    tenant_key: str,
    session_key: str,
    turn: dict[str, Any],
    index: int,
    default_wait_s: float,
) -> dict[str, Any]:
    message = str(turn.get("user") or "")
    media = list(turn.get("media") or [])
    phase = turn.get("phase") or "measured"
    measure = bool(turn.get("measure", phase == "measured"))
    before = await _snapshot(container, tenant_key, session_key)

    if media:
        await _record_uploaded_images(
            container,
            tenant_key=tenant_key,
            session_key=session_key,
            media=media,
            message_id=None,
            query=message,
        )

    capture = TurnCapture()
    restore = wrap_all_tool_registries(capture)
    prompt_captures: list[dict[str, Any]] = []
    attention_captures: list[dict[str, Any]] = []
    prompt_capture_chars = int(turn.get("capture_prompt_chars") or (50000 if index == 1 else 12000))
    reply_parts: list[str] = []
    first_token_parts: list[str] = []
    first_token_status = "disabled"
    first_token_detail = ""
    error: str | None = None

    async def _on_token(token: str) -> None:
        capture.mark_first_token()
        reply_parts.append(token)

    async def _on_first_token(token: str) -> None:
        capture.mark_first_token()
        first_token_parts.append(token)

    async def _on_first_token_status(status: str, **payload: Any) -> None:
        nonlocal first_token_status, first_token_detail
        first_token_status = status
        detail = payload.get("detail")
        if detail:
            first_token_detail = str(detail)

    def _on_prompt_messages(messages: list[dict]) -> None:
        prompt_captures.append(_summarize_prompt_messages(
            messages,
            call_no=len(prompt_captures) + 1,
            max_chars=max(200, prompt_capture_chars),
        ))

    def _on_attention_packets(packets: list[AttentionPacket]) -> None:
        attention_captures.append({
            "call": len(attention_captures) + 1,
            "packets": _summarize_attention_packets(packets),
        })

    try:
        reply = await container.subagent_store.run_turn(
            session_key=session_key,
            tenant_key=tenant_key,
            message=message,
            on_token=_on_token,
            media=media,
            message_id=None,
            origin_session_key=f"main:{tenant_key}",
            first_token_agent=getattr(container, "first_token_agent", None),
            on_first_token=_on_first_token,
            on_first_token_status=_on_first_token_status,
            on_prompt_messages=_on_prompt_messages if bool(turn.get("capture_prompt", True)) else None,
            on_attention_packets=_on_attention_packets if bool(turn.get("capture_prompt", True)) else None,
        )
        capture.mark_done()
    except Exception as exc:
        error = str(exc)
        capture.mark_done()
        reply = "".join(reply_parts)
    finally:
        restore()

    if not reply:
        reply = "".join(reply_parts)

    wait_s = float(turn.get("wait_side_effects_s", default_wait_s) or 0)
    if wait_s > 0:
        await asyncio.sleep(wait_s)
    after = await _snapshot(container, tenant_key, session_key)
    after = await _wait_until_checks(
        container,
        tenant_key=tenant_key,
        session_key=session_key,
        before=before,
        after=after,
        turn=turn,
        reply=reply,
        error=error,
        capture=capture,
    )
    doc_delta = _doc_delta(before, after)
    runtime_tasks_created = _runtime_tasks_created(before, after)
    image_jobs_created = _image_jobs_created(before, after)
    cron_jobs_created = _cron_jobs_created(before, after)
    session_delta = _session_delta(before, after)
    subagent_session_delta = _subagent_session_delta(before, after)

    return {
        "turn": index,
        "user": message,
        "media": media,
        "phase": phase,
        "measure": measure,
        "reply": reply,
        "first_token_reply": "".join(first_token_parts).strip(),
        "first_token_status": first_token_status,
        "first_token_detail": first_token_detail,
        "main_reply": "".join(reply_parts).strip() or reply,
        "error": error,
        "ttft_ms": capture.ttft_ms,
        "total_ms": capture.total_ms,
        "tools_called": capture.tools_called,
        "tools": [item.__dict__ for item in capture.tools],
        "prompt_captures": prompt_captures,
        "attention_captures": attention_captures,
        "runtime_tasks_created": runtime_tasks_created,
        "image_jobs_created": image_jobs_created,
        "cron_jobs_created": cron_jobs_created,
        "docs_before": before["docs"],
        "docs_after": after["docs"],
        "doc_delta": doc_delta,
        "topic_state_before": before.get("topic_state"),
        "topic_state_after": after.get("topic_state"),
        "runtime_tasks_after": after["runtime_tasks"],
        "image_jobs_before": before["image_jobs"],
        "image_jobs_after": after["image_jobs"],
        "cron_jobs_before": before["cron_jobs"],
        "cron_jobs_after": after["cron_jobs"],
        "session_before": before["session"],
        "session_after": after["session"],
        "session_delta": session_delta,
        "subagent_sessions_before": before["subagent_sessions"],
        "subagent_sessions_after": after["subagent_sessions"],
        "subagent_session_delta": subagent_session_delta,
        "skin_diary_results_created": _skin_diary_results_created(before, after),
        "skin_diary_results_before": before["skin_diary_results"],
        "skin_diary_results_after": after["skin_diary_results"],
    }


async def _wait_until_checks(
    container,
    *,
    tenant_key: str,
    session_key: str,
    before: dict[str, Any],
    after: dict[str, Any],
    turn: dict[str, Any],
    reply: str,
    error: str | None,
    capture: TurnCapture,
) -> dict[str, Any]:
    wait_until = turn.get("wait_until") or []
    if not wait_until:
        return after

    timeout_s = float(turn.get("wait_until_timeout_s") or 60)
    interval_s = float(turn.get("wait_until_interval_s") or 2)
    deadline = time.monotonic() + max(0, timeout_s)

    while True:
        probe = _probe_turn_result(
            before=before,
            after=after,
            reply=reply,
            error=error,
            capture=capture,
        )
        checks = evaluate_checks(wait_until, probe)
        if checks and all(item.passed for item in checks):
            return after
        if time.monotonic() >= deadline:
            return after
        await asyncio.sleep(max(0.1, interval_s))
        after = await _snapshot(container, tenant_key, session_key)


def _probe_turn_result(
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    reply: str,
    error: str | None,
    capture: TurnCapture,
) -> dict[str, Any]:
    return {
        "reply": reply,
        "first_token_reply": "",
        "first_token_status": "",
        "first_token_detail": "",
        "main_reply": reply,
        "error": error,
        "ttft_ms": capture.ttft_ms,
        "total_ms": capture.total_ms,
        "tools_called": capture.tools_called,
        "tools": [item.__dict__ for item in capture.tools],
        "runtime_tasks_created": _runtime_tasks_created(before, after),
        "image_jobs_created": _image_jobs_created(before, after),
        "cron_jobs_created": _cron_jobs_created(before, after),
        "docs_before": before["docs"],
        "docs_after": after["docs"],
        "doc_delta": _doc_delta(before, after),
        "topic_state_before": before.get("topic_state"),
        "topic_state_after": after.get("topic_state"),
        "runtime_tasks_after": after["runtime_tasks"],
        "image_jobs_before": before["image_jobs"],
        "image_jobs_after": after["image_jobs"],
        "cron_jobs_before": before["cron_jobs"],
        "cron_jobs_after": after["cron_jobs"],
        "session_before": before["session"],
        "session_after": after["session"],
        "session_delta": _session_delta(before, after),
        "subagent_sessions_before": before["subagent_sessions"],
        "subagent_sessions_after": after["subagent_sessions"],
        "subagent_session_delta": _subagent_session_delta(before, after),
        "skin_diary_results_created": _skin_diary_results_created(before, after),
        "skin_diary_results_before": before["skin_diary_results"],
        "skin_diary_results_after": after["skin_diary_results"],
    }


async def _memory_entries(container, tenant_key: str) -> list[dict[str, Any]]:
    async with container.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT topic, description, content, source, memory_type
                  FROM nb_memory_entries
                 WHERE tenant_key = %s
                 ORDER BY source, topic
                """,
                (tenant_key,),
            )
            rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for topic, description, content, source, memory_type in rows:
        # is_skin 以 DB memory_type 列为准（seed/extract 都把 skin 写进该列），
        # 不靠扫文本——否则 column 形态的 skin 条目会被误判成非 skin。
        out.append({
            "topic": str(topic or ""),
            "description": str(description or ""),
            "content": str(content or ""),
            "source": str(source or ""),
            "memory_type": str(memory_type or "chitchat"),
            "is_skin": str(memory_type or "").strip() == "skin",
        })
    return out


async def _memory_ledgers(container, tenant_key: str) -> list[dict[str, Any]]:
    async with container.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT ledger_id, status, dream_status, metadata_json
                  FROM nb_memory_ledgers
                 WHERE tenant_key = %s
                 ORDER BY created_at ASC
                """,
                (tenant_key,),
            )
            rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for ledger_id, status, dream_status, metadata_json in rows:
        try:
            metadata = json.loads(metadata_json) if metadata_json else {}
        except (TypeError, ValueError):
            metadata = {}
        out.append({
            "ledger_id": str(ledger_id or ""),
            "status": str(status or ""),
            "dream_status": str(dream_status or ""),
            "guardrail": metadata.get("guardrail") if isinstance(metadata, dict) else None,
        })
    return out


async def _dream_artifacts(container, tenant_key: str) -> list[dict[str, Any]]:
    async with container.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT artifact_key, status, applied_at, content
                  FROM nb_subagent_artifacts
                 WHERE tenant_key = %s
                   AND artifact_key LIKE 'memory-ledger:%%'
                 ORDER BY created_at ASC
                """,
                (tenant_key,),
            )
            rows = await cur.fetchall()
    return [
        {
            "artifact_key": str(artifact_key or ""),
            "status": str(status or ""),
            "applied": applied_at is not None,
            "content": str(content or ""),
        }
        for artifact_key, status, applied_at, content in rows
    ]


async def _snapshot(container, tenant_key: str, session_key: str) -> dict[str, Any]:
    docs: dict[str, Any] = {}
    for name in ("USER.md", "SOUL.md", "SKIN_DIARY_TODO.md"):
        content = await container.doc_repo.get(tenant_key, name)
        docs[name] = {
            "exists": content is not None,
            "chars": len(content or ""),
            "content": content or "",
        }

    tasks = await container.runtime_task_repo.list_recent(
        tenant_key=tenant_key,
        limit=100,
    )
    topic_state = None
    topic_repo = getattr(container, "topic_repo", None)
    if topic_repo is not None:
        try:
            topic_state = await topic_repo.get(tenant_key)
            if topic_state is None and session_key != tenant_key:
                topic_state = await topic_repo.get(session_key)
        except Exception:
            topic_state = None
    return {
        "docs": docs,
        "runtime_tasks": tasks,
        "topic_state": topic_state,
        "image_jobs": await _image_jobs(container, tenant_key),
        "cron_jobs": await _cron_jobs(container, tenant_key),
        "skin_diary_results": await _skin_diary_results(container, tenant_key),
        "session": await _session_messages(container, tenant_key, session_key),
        "subagent_sessions": {
            "skin_diary": await _session_messages(container, tenant_key, f"skin_diary:{tenant_key}"),
            "deep_report": await _session_messages(container, tenant_key, f"deep_report:{tenant_key}"),
        },
        "memory_entries": await _memory_entries(container, tenant_key),
        "memory_ledgers": await _memory_ledgers(container, tenant_key),
        "dream_artifacts": await _dream_artifacts(container, tenant_key),
    }


def _doc_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"docs": {}}
    for name, before_doc in before.get("docs", {}).items():
        after_doc = (after.get("docs") or {}).get(name) or {}
        before_content = before_doc.get("content") or ""
        after_content = after_doc.get("content") or ""
        out["docs"][name] = {
            "changed": before_content != after_content,
            "before_chars": len(before_content),
            "after_chars": len(after_content),
        }
    return out


def _runtime_tasks_created(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    before_ids = {item.get("task_id") for item in before.get("runtime_tasks", [])}
    created = []
    for item in after.get("runtime_tasks", []):
        if item.get("task_id") not in before_ids:
            created.append(item)
    return list(reversed(created))


def _image_jobs_created(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    before_ids = {item.get("job_id") for item in before.get("image_jobs", [])}
    created = []
    for item in after.get("image_jobs", []):
        if item.get("job_id") not in before_ids:
            created.append(item)
    return list(reversed(created))


def _cron_jobs_created(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    before_ids = {item.get("id") for item in before.get("cron_jobs", [])}
    created = []
    for item in after.get("cron_jobs", []):
        if item.get("id") not in before_ids:
            created.append(item)
    return created


def _skin_diary_results_created(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    before_ids = {item.get("id") for item in before.get("skin_diary_results", [])}
    created = []
    for item in after.get("skin_diary_results", []):
        if item.get("id") not in before_ids:
            created.append(item)
    return list(reversed(created))


def _session_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_session = before.get("session") or {}
    after_session = after.get("session") or {}
    before_count = int(before_session.get("message_count") or 0)
    after_count = int(after_session.get("message_count") or 0)
    before_last_seq = before_session.get("last_seq")
    return {
        "message_count_before": before_count,
        "message_count_after": after_count,
        "message_count_delta": after_count - before_count,
        "new_messages": _new_session_messages(after_session, before_last_seq),
    }


def _subagent_session_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    before_sessions = before.get("subagent_sessions") or {}
    after_sessions = after.get("subagent_sessions") or {}
    for name, before_session in before_sessions.items():
        after_session = after_sessions.get(name) or {}
        before_count = int((before_session or {}).get("message_count") or 0)
        after_count = int(after_session.get("message_count") or 0)
        before_last_seq = (before_session or {}).get("last_seq")
        out[name] = {
            "message_count_before": before_count,
            "message_count_after": after_count,
            "message_count_delta": after_count - before_count,
            "new_messages": _new_session_messages(after_session, before_last_seq),
        }
    return out


def _new_session_messages(after_session: dict[str, Any], before_last_seq: Any) -> list[dict[str, Any]]:
    try:
        last_seq = int(before_last_seq) if before_last_seq is not None else -1
    except (TypeError, ValueError):
        last_seq = -1
    out: list[dict[str, Any]] = []
    for item in after_session.get("recent_messages") or []:
        if not isinstance(item, dict):
            continue
        try:
            seq = int(item.get("seq")) if item.get("seq") is not None else -1
        except (TypeError, ValueError):
            seq = -1
        if seq > last_seq:
            out.append(item)
    return out


def _maybe_wrap_prompt_capture(
    loop,
    *,
    enabled: bool,
    captures: list[dict[str, Any]],
    max_chars: int,
):
    if not enabled:
        return lambda: None

    original = loop._get_messages_async

    async def _capturing_get_messages_async():
        messages = await original()
        captures.append(_summarize_prompt_messages(
            messages,
            call_no=len(captures) + 1,
            max_chars=max(200, max_chars),
        ))
        return messages

    loop._get_messages_async = _capturing_get_messages_async

    def restore() -> None:
        loop._get_messages_async = original

    return restore


def _maybe_wrap_attention_capture(
    loop,
    *,
    enabled: bool,
    captures: list[dict[str, Any]],
):
    builder = getattr(loop, "context_builder", None)
    if not enabled or builder is None or not hasattr(builder, "_collect_attention_packets"):
        return lambda: None

    original = builder._collect_attention_packets

    async def _capturing_collect_attention_packets(ctx, *, attention_packets):
        packets = await original(ctx, attention_packets=attention_packets)
        captures.append({
            "call": len(captures) + 1,
            "packets": _summarize_attention_packets(packets),
        })
        return packets

    builder._collect_attention_packets = _capturing_collect_attention_packets

    def restore() -> None:
        builder._collect_attention_packets = original

    return restore


def _summarize_attention_packets(packets: list[AttentionPacket]) -> list[dict[str, Any]]:
    return [_summarize_attention_packet(packet) for packet in packets]


def _summarize_attention_packet(packet: AttentionPacket) -> dict[str, Any]:
    content = packet.content
    return {
        "source": packet.source,
        "placement": packet.placement,
        "lifetime": packet.lifetime,
        "role": packet.role,
        "priority": packet.priority,
        "metadata": packet.metadata,
        "content_kind": _prompt_content_kind(content),
        "content_chars": len(_prompt_content_to_text(content)),
        "content_preview": _truncate_debug_text(_prompt_content_to_text(content), 2000),
    }


def _summarize_prompt_messages(
    messages: list[dict[str, Any]],
    *,
    call_no: int,
    max_chars: int,
) -> dict[str, Any]:
    rendered = [_summarize_prompt_message(idx, msg, max_chars=max_chars) for idx, msg in enumerate(messages)]
    joined = "\n".join(item.get("text_preview") or "" for item in rendered)
    return {
        "call": call_no,
        "message_count": len(messages),
        "attention_detected": {
            "evidence_retrieval_hint": "【需要补充证据】" in joined,
            "topic_reminder": (
                "【当前正在聊】" in joined
                or "【代聊点】" in joined
                or "当前用户状态偏【" in joined
            ),
            "image_upload_state": "【本轮图片状态】" in joined,
            "first_token_opener": (
                "【本轮你已经发送给用户的第一句回复】" in joined
                or "【已发送给用户的接话短句】" in joined
                or "【本轮已经发送给用户的开场白】" in joined
            ),
            "fetched_historical_image": "【系统补充的历史图片】" in joined,
        },
        "messages": rendered,
    }


def _summarize_prompt_message(idx: int, msg: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    role = str(msg.get("role") or "")
    content = msg.get("content")
    text = _prompt_content_to_text(content)
    item: dict[str, Any] = {
        "idx": idx,
        "role": role,
        "content_kind": _prompt_content_kind(content),
        "text_chars": len(text),
        "text_preview": _truncate_debug_text(text, max_chars),
        "text_full": text,
    }
    if role == "system":
        stable_prefix = str(msg.get("_cache_stable_prefix") or "")
        dynamic_tail = str(msg.get("_cache_dynamic_tail") or "")
        item["stable_prefix_chars"] = len(stable_prefix)
        item["dynamic_tail_chars"] = len(dynamic_tail)
        item["stable_prefix_preview"] = _truncate_debug_text(
            stable_prefix,
            max_chars,
        )
        item["stable_prefix_full"] = stable_prefix
        item["dynamic_tail_preview"] = _truncate_debug_text(
            dynamic_tail,
            max_chars,
        )
        item["dynamic_tail_full"] = dynamic_tail
    tool_calls = msg.get("tool_calls") or []
    if tool_calls:
        item["tool_calls"] = [
            {
                "id": call.get("id"),
                "name": ((call.get("function") or {}).get("name") or call.get("name")),
                "arguments": ((call.get("function") or {}).get("arguments") or call.get("arguments")),
            }
            for call in tool_calls
        ]
    return item


def _prompt_content_kind(content: Any) -> str:
    if isinstance(content, list):
        return "multimodal"
    if isinstance(content, str):
        return "text"
    return type(content).__name__


def _prompt_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            item_type = str(item.get("type") or "")
            if item_type in {"text", "input_text"}:
                parts.append(str(item.get("text") or ""))
                continue
            image_url = item.get("image_url")
            if isinstance(image_url, dict):
                parts.append(f"[image_url] {image_url.get('url') or ''}")
            elif image_url:
                parts.append(f"[image_url] {image_url}")
            elif item_type in {"image_url", "input_image"}:
                parts.append("[image_url]")
            else:
                parts.append(json.dumps(item, ensure_ascii=False, default=str))
        return "\n".join(part for part in parts if part)
    return json.dumps(content, ensure_ascii=False, default=str)


_ATTENTION_REPORT_SOURCES: dict[str, dict[str, Any]] = {
    "evidence_retrieval_hint": {
        "provider": "EvidenceAttentionProvider",
        "placement": "before_last_user",
        "lifetime": "one_turn",
        "priority": 12,
        "markers": ("【需要补充证据】",),
    },
    "image_upload_state": {
        "provider": "ImageUploadAttentionProvider",
        "placement": "after_history",
        "lifetime": "one_turn",
        "priority": 10,
        "markers": ("【本轮图片状态】",),
    },
    "topic_reminder": {
        "provider": "TopicAttentionProvider",
        "placement": "before_last_user",
        "lifetime": "until_changed",
        "priority": 20,
        "markers": ("【当前正在聊】", "【代聊点】", "当前用户状态偏【"),
    },
    "first_token_opener": {
        "provider": "scenario_runtime",
        "placement": "tail",
        "lifetime": "one_turn",
        "priority": 70,
        "markers": (
            "【本轮你已经发送给用户的第一句回复】",
            "【已发送给用户的接话短句】",
            "【本轮已经发送给用户的开场白】",
        ),
    },
    "fetched_historical_image": {
        "provider": "tool_result_bridge",
        "placement": "history",
        "lifetime": "one_turn",
        "priority": None,
        "markers": ("【系统补充的历史图片】",),
    },
}


def _compact_report_output(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "report_format": "scenario_compact_v2",
        "scenario": output.get("scenario"),
        "agent": output.get("agent"),
        "tenant_key": output.get("tenant_key"),
        "session_key": output.get("session_key"),
        "verdict": output.get("verdict"),
        "turns": [
            _compact_turn_for_report(turn)
            for turn in output.get("turns", [])
            if isinstance(turn, dict)
        ],
        "dialogue_path": output.get("dialogue_path"),
    }


def _compact_turn_for_report(turn: dict[str, Any]) -> dict[str, Any]:
    topic_before = turn.get("topic_state_before")
    topic_after = turn.get("topic_state_after")
    return {
        "turn": turn.get("turn"),
        "user": turn.get("user"),
        "media": turn.get("media") or [],
        "phase": turn.get("phase"),
        "measure": turn.get("measure"),
        "verdict": turn.get("verdict"),
        "error": turn.get("error"),
        "timing": {
            "ttft_ms": turn.get("ttft_ms"),
            "total_ms": turn.get("total_ms"),
            "first_token_status": turn.get("first_token_status"),
            "first_token_detail": turn.get("first_token_detail"),
        },
        "reply_preview": _truncate_debug_text(str(turn.get("reply") or ""), 1600),
        "first_token_reply": str(turn.get("first_token_reply") or ""),
        "tools_called": turn.get("tools_called") or [],
        "tools": [_compact_tool_call(item) for item in turn.get("tools") or []],
        "runtime_tasks_created": _compact_runtime_tasks(turn.get("runtime_tasks_created") or [], limit=50),
        "business_jobs": {
            "image_jobs_created": _compact_items(
                turn.get("image_jobs_created") or [],
                keys=(
                    "job_id", "session_key", "image_id", "focus", "status",
                    "external_job_id", "last_error", "created_at", "updated_at",
                    "started_at", "completed_at",
                ),
                limit=20,
            ),
            "cron_jobs_created": _compact_items(
                turn.get("cron_jobs_created") or [],
                keys=("id", "session_key", "cron_type", "interval_s", "run_at", "task", "status"),
                limit=20,
            ),
            "skin_diary_results_created": _compact_items(
                turn.get("skin_diary_results_created") or [],
                keys=("id", "state", "summary", "analyzed_at", "create_time", "creator"),
                limit=10,
            ),
        },
        "state_changes": {
            "docs_changed": _docs_changed(turn.get("doc_delta") or {}),
            "topic_changed": topic_before != topic_after,
            "topic_state_after": _compact_topic_state(topic_after),
            "topic_reminder_after": _build_topic_reminder_preview(topic_after),
            "session_delta": turn.get("session_delta") or {},
            "subagent_session_delta": turn.get("subagent_session_delta") or {},
        },
        "prompt": _compact_prompt_captures_for_report(
            turn.get("prompt_captures") or [],
            full=turn.get("turn") == 1,
        ),
        "attention": _compact_attention_captures_for_report(
            turn.get("attention_captures") or [],
        ),
        "hard_assertions": turn.get("hard_assertions") or [],
        "soft_checks": turn.get("soft_checks") or [],
    }


def _session_delta_assistant_log_lines(turn: dict[str, Any]) -> list[str]:
    """Surface persisted assistant messages that arrived during post-turn waits."""
    reply = str(turn.get("reply") or "")
    lines: list[str] = []
    session_delta = turn.get("session_delta") or {}
    messages = session_delta.get("new_messages") or []
    for msg in messages:
        if str(msg.get("role") or "") != "assistant":
            continue
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        # The normal turn response is already represented by reply_preview. These
        # lines are mainly for background activations saved while wait_until polls.
        if content in reply:
            continue
        seq = msg.get("seq")
        tool_name = str(msg.get("tool_name") or "").strip()
        label = "SESSION assistant"
        if tool_name:
            label = f"{label} tool={tool_name}"
        lines.append(f"  {label} seq={seq} text={_truncate_debug_text(content, 240)}")
    return lines


def _compact_tool_call(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"raw": str(item)}
    out = {
        key: item.get(key)
        for key in ("tool_name", "ok", "duration_ms", "tool_call_id", "action", "status", "error")
        if item.get(key) not in (None, "", {})
    }
    arguments = item.get("arguments")
    if arguments not in (None, "", {}, []):
        out["arguments"] = arguments
    result = item.get("result")
    if isinstance(result, dict):
        out["result"] = {
            key: value
            for key, value in result.items()
            if value not in (None, "", {}, [])
        }
    elif result not in (None, ""):
        out["result"] = result
    return out


def _compact_runtime_tasks(items: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    return _compact_items(
        items,
        keys=(
            "task_id", "task_type", "stream_name", "status", "attempt", "max_attempts",
            "queue_message_id", "claimed_by", "scope_key", "last_error",
            "created_at", "updated_at", "completed_at",
        ),
        limit=limit,
    )


def _compact_items(
    items: list[Any],
    *,
    keys: tuple[str, ...],
    limit: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            out.append({"raw": str(item)})
            continue
        compact = {
            key: _compact_scalar(item.get(key))
            for key in keys
            if item.get(key) not in (None, "", {}, [])
        }
        out.append(compact)
    return out


def _compact_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return _truncate_debug_text(value, 500)
    return value


def _docs_changed(doc_delta: dict[str, Any]) -> list[dict[str, Any]]:
    changed: list[dict[str, Any]] = []
    for name, info in (doc_delta.get("docs") or {}).items():
        if not isinstance(info, dict) or not info.get("changed"):
            continue
        changed.append({
            "name": name,
            "before_chars": info.get("before_chars"),
            "after_chars": info.get("after_chars"),
        })
    return changed


def _compact_topic_state(state: Any) -> dict[str, Any] | None:
    if not isinstance(state, dict):
        return None
    topics = state.get("topics") or {}
    if not isinstance(topics, dict):
        topics = {}
    compact_topics: dict[str, Any] = {}
    for label, info in topics.items():
        if not isinstance(info, dict):
            continue
        compact_topics[str(label)] = {
            key: info.get(key)
            for key in ("status", "pending_kind", "turns", "last_turn", "first_pending_turn", "hook")
            if info.get(key) not in (None, "", {}, [])
        }
    return {
        "total_turns": state.get("total_turns"),
        "last_reminder_turn": state.get("last_reminder_turn"),
        "last_memory_extract_turn": state.get("last_memory_extract_turn"),
        "mood": state.get("mood"),
        "topics": compact_topics,
    }


def _build_topic_reminder_preview(state: Any) -> str:
    if not isinstance(state, dict):
        return ""
    try:
        from Mojing.agent.cold_path import build_reminder
        return _truncate_debug_text(build_reminder(state), 1600)
    except Exception:
        return ""


def _compact_prompt_captures_for_report(
    captures: list[dict[str, Any]],
    *,
    full: bool,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for capture in captures:
        if not isinstance(capture, dict):
            continue
        messages = capture.get("messages") or []
        if not isinstance(messages, list):
            messages = []
        out.append({
            "call": capture.get("call"),
            "message_count": capture.get("message_count"),
            "attention_detected": capture.get("attention_detected") or {},
            "attention_packets": _attention_packets_from_prompt(messages),
            "messages": _compact_prompt_messages_for_report(messages, full=full),
        })
    return out


def _compact_attention_captures_for_report(captures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for capture in captures:
        if not isinstance(capture, dict):
            continue
        packets = capture.get("packets") or []
        compact_packets: list[dict[str, Any]] = []
        for packet in packets:
            if not isinstance(packet, dict):
                continue
            compact_packets.append({
                "source": packet.get("source"),
                "placement": packet.get("placement"),
                "lifetime": packet.get("lifetime"),
                "role": packet.get("role"),
                "priority": packet.get("priority"),
                "content_kind": packet.get("content_kind"),
                "content_chars": packet.get("content_chars"),
                "content_preview": _truncate_debug_text(
                    str(packet.get("content_preview") or ""),
                    3000,
                ),
                "metadata": packet.get("metadata") or {},
            })
        out.append({
            "call": capture.get("call"),
            "packets": compact_packets,
        })
    return out


def _attention_packets_from_prompt(messages: list[Any]) -> list[dict[str, Any]]:
    joined_messages = [
        item for item in messages
        if isinstance(item, dict)
    ]
    out: list[dict[str, Any]] = []
    for source, spec in _ATTENTION_REPORT_SOURCES.items():
        preview = _find_attention_preview(joined_messages, spec.get("markers") or ())
        out.append({
            "source": source,
            "provider": spec.get("provider"),
            "placement": spec.get("placement"),
            "lifetime": spec.get("lifetime"),
            "priority": spec.get("priority"),
            "emitted": bool(preview),
            "content_preview": preview,
        })
    return out


def _find_attention_preview(messages: list[dict[str, Any]], markers: tuple[str, ...]) -> str:
    for item in messages:
        text = str(item.get("text_preview") or "")
        if any(marker in text for marker in markers):
            return _truncate_debug_text(text, 1200)
    return ""


def _compact_prompt_messages_for_report(messages: list[Any], *, full: bool) -> list[dict[str, Any]]:
    normalized = [item for item in messages if isinstance(item, dict)]
    if full:
        return normalized

    last_user_idx = None
    for item in normalized:
        if item.get("role") == "user":
            last_user_idx = item.get("idx")

    compact: list[dict[str, Any]] = []
    for item in normalized:
        idx = item.get("idx")
        role = item.get("role")
        source = _attention_source_for_text(str(item.get("text_preview") or ""))
        has_main_system_cache = bool(item.get("stable_prefix_chars") or item.get("dynamic_tail_chars"))
        has_tool_calls = bool(item.get("tool_calls"))
        keep = (
            has_main_system_cache
            or bool(source)
            or has_tool_calls
            or role == "tool"
            or idx == last_user_idx
        )
        if not keep:
            continue
        compact_item: dict[str, Any] = {
            "idx": idx,
            "role": role,
            "content_kind": item.get("content_kind"),
        }
        if has_main_system_cache:
            compact_item["stable_prefix_chars"] = item.get("stable_prefix_chars")
            compact_item["dynamic_tail_chars"] = item.get("dynamic_tail_chars")
            compact_item["dynamic_tail_preview"] = item.get("dynamic_tail_preview")
            compact_item["stable_prefix_full"] = item.get("stable_prefix_full")
            compact_item["dynamic_tail_full"] = item.get("dynamic_tail_full")
            compact_item["text_full"] = item.get("text_full")
        elif source:
            compact_item["attention_source"] = source
            compact_item["text_preview"] = item.get("text_preview")
            compact_item["text_full"] = item.get("text_full")
        else:
            compact_item["text_preview"] = _truncate_debug_text(
                str(item.get("text_preview") or ""),
                1600,
            )
            compact_item["text_full"] = item.get("text_full")
        if has_tool_calls:
            compact_item["tool_calls"] = item.get("tool_calls")
        compact.append(compact_item)
    return compact


def _attention_source_for_text(text: str) -> str:
    for source, spec in _ATTENTION_REPORT_SOURCES.items():
        if any(marker in text for marker in spec.get("markers") or ()):
            return source
    return ""


def _truncate_debug_text(text: str, max_chars: int) -> str:
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[truncated {len(text) - max_chars} chars]"


async def _capture_first_token_prompt(
    first_token_agent: Any,
    *,
    tenant_key: str,
    session_key: str,
    user_message: str,
    history: list[Any],
    consolidated_from: int,
    history_offset: int | None = None,
    agent_lane: str | None = None,
    prompt_surface: str = "app",
) -> dict[str, Any] | None:
    if not getattr(first_token_agent, "enabled", False):
        return None
    if not str(user_message or "").strip():
        return None

    resolved_lane = _normalize_agent_lane(agent_lane or _infer_agent_lane(session_key))
    system_prompt, prompt_fingerprint = first_token_agent._prompt_for_lane(resolved_lane, prompt_surface)
    if not system_prompt.strip():
        return None

    shared = _build_shared_context(
        history,
        consolidated_from=consolidated_from,
        history_offset=history_offset,
    )
    del tenant_key, prompt_fingerprint
    messages = first_token_agent._build_input(
        shared,
        user_message,
        system_prompt,
        agent_lane=resolved_lane,
        prompt_surface=prompt_surface,
    )
    return {
        "agent_lane": resolved_lane,
        "cache_mode": "prefix",
        "context_version": shared.context_version,
        "active_window_chars": len(shared.active_window_text),
        "last_reply_chars": len(shared.last_assistant_reply),
        "messages": _summarize_prompt_messages(
            messages,
            call_no=1,
            max_chars=50000,
        ),
    }


def _render_prompt_capture_markdown(title: str, capture: dict[str, Any]) -> str:
    lines: list[str] = [title, ""]
    if capture.get("agent_lane"):
        lines.append(f"agent_lane: {capture.get('agent_lane')}")
    if capture.get("cache_hit") is not None:
        lines.append(f"cache_hit: {capture.get('cache_hit')}")
    if capture.get("cache_mode"):
        lines.append(f"cache_mode: {capture.get('cache_mode')}")
    if capture.get("previous_response_id"):
        lines.append(f"previous_response_id: {capture.get('previous_response_id')}")
    if capture.get("context_version") is not None:
        lines.append(f"context_version: {capture.get('context_version')}")
    lines.append(f"message_count: {((capture.get('messages') or {}).get('message_count') if isinstance(capture.get('messages'), dict) else capture.get('message_count'))}")
    lines.append("")

    messages_block = capture.get("messages") if isinstance(capture.get("messages"), dict) else capture
    if isinstance(messages_block, dict):
        attention_detected = messages_block.get("attention_detected")
        if attention_detected:
            lines.append(f"attention_detected: {json.dumps(attention_detected, ensure_ascii=False)}")
            lines.append("")
        messages = messages_block.get("messages") or []
    else:
        messages = []

    lines.append("Messages")
    for item in messages:
        if not isinstance(item, dict):
            continue
        lines.append(f"idx={item.get('idx')} role={item.get('role')}")
        if item.get("stable_prefix_full"):
            lines.append("stable_prefix_full")
            lines.append(str(item.get("stable_prefix_full") or ""))
        if item.get("dynamic_tail_full"):
            lines.append("")
            lines.append("dynamic_tail_full")
            lines.append(str(item.get("dynamic_tail_full") or ""))
        if item.get("text_full") and not item.get("stable_prefix_full"):
            lines.append("text_full")
            lines.append(str(item.get("text_full") or ""))
        tool_calls = item.get("tool_calls") or []
        if tool_calls:
            lines.append("")
            lines.append("tool_calls")
            lines.append(json.dumps(tool_calls, ensure_ascii=False, indent=2))
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _render_turn_review_markdown(turn: dict[str, Any]) -> str:
    topic_after = turn.get("topic_state_after")
    lines = [
        f"turn: {turn.get('turn')}",
        f"user: {turn.get('user')}",
        f"ttft_ms: {turn.get('ttft_ms')}",
        f"total_ms: {turn.get('total_ms')}",
        "",
        "first_token_reply",
        str(turn.get("first_token_reply") or ""),
        "",
        "main_reply",
        str(turn.get("main_reply") or ""),
        "",
        "reply",
        str(turn.get("reply") or ""),
        "",
        f"tools_called: {json.dumps(turn.get('tools_called') or [], ensure_ascii=False)}",
    ]
    tools = turn.get("tools") or []
    if tools:
        lines.extend(["", "tools"])
        for tool in tools:
            lines.append(json.dumps(tool, ensure_ascii=False, indent=2, default=str))
    runtime_tasks_created = turn.get("runtime_tasks_created") or []
    if runtime_tasks_created:
        lines.extend(["", "runtime_tasks_created"])
        for item in runtime_tasks_created:
            lines.append(json.dumps(item, ensure_ascii=False, indent=2, default=str))
    doc_delta = turn.get("doc_delta") or {}
    lines.extend(["", "postprocess_doc_delta", json.dumps(doc_delta, ensure_ascii=False, indent=2, default=str)])
    lines.extend([
        "",
        "topic_state_after",
        json.dumps(_compact_topic_state(topic_after), ensure_ascii=False, indent=2, default=str),
        "",
        "topic_reminder_after",
        _build_topic_reminder_preview(topic_after),
    ])
    return "\n".join(lines).strip() + "\n"


def _render_dialogue_markdown(output: dict[str, Any]) -> str:
    lines = [
        f"# Dialogue Log: {output.get('scenario')}",
        "",
        f"- verdict: {output.get('verdict')}",
        f"- agent: {output.get('agent')}",
        f"- tenant_key: {output.get('tenant_key')}",
        f"- session_key: {output.get('session_key')}",
    ]

    for turn in output.get("turns") or []:
        if not isinstance(turn, dict):
            continue
        lines.extend([
            "",
            f"## Turn {turn.get('turn')}",
            "",
            f"- verdict: {turn.get('verdict')}",
            f"- ttft_ms: {turn.get('ttft_ms')}",
            f"- total_ms: {turn.get('total_ms')}",
            "",
        ])
        _append_dialogue_input(lines, turn)
        lines.extend([
            "",
            "### Assistant",
            str(turn.get("reply") or "").strip() or "-",
            "",
            "### Tool Calls",
        ])
        tools = turn.get("tools") or []
        if tools:
            lines.extend(_format_dialogue_tools(tools))
        else:
            lines.append("- none")

        runtime_tasks_created = turn.get("runtime_tasks_created") or []
        if runtime_tasks_created:
            lines.extend(["", "### Runtime Tasks Created"])
            for task in runtime_tasks_created:
                lines.append(_format_dialogue_runtime_task(task))

        hard_assertions = turn.get("hard_assertions") or []
        if hard_assertions:
            passed = sum(1 for item in hard_assertions if isinstance(item, dict) and item.get("passed"))
            lines.extend([
                "",
                "### Hard Assertions",
                f"- {passed}/{len(hard_assertions)} passed",
            ])

    return "\n".join(lines).strip() + "\n"


def _append_dialogue_input(lines: list[str], turn: dict[str, Any]) -> None:
    user = str(turn.get("user") or "")
    if user.startswith("[设备/系统事件]"):
        lines.append("### System Event")
        lines.append(user)
    else:
        lines.append("### User")
        lines.append(user)

    media = turn.get("media") or []
    if media:
        lines.extend(["", "### Media"])
        for item in media:
            if isinstance(item, dict):
                url = item.get("url") or item.get("source") or item.get("image_url")
                lines.append(f"- {url or json.dumps(item, ensure_ascii=False, default=str)}")
            else:
                lines.append(f"- {item}")


def _format_dialogue_tools(tools: list[Any]) -> list[str]:
    lines: list[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            lines.append(f"- {tool}")
            continue
        segments = [str(tool.get("tool_name") or "unknown_tool")]
        arguments = tool.get("arguments")
        if arguments not in (None, {}, [], ""):
            segments.append(f"args={json.dumps(arguments, ensure_ascii=False, default=str)}")
        for key in ("action", "status", "ok"):
            if tool.get(key) not in (None, "", {}, []):
                segments.append(f"{key}={tool.get(key)}")
        result = tool.get("result")
        if isinstance(result, dict):
            focus = result.get("message_focus")
            if focus:
                segments.append(f"focus={focus}")
        lines.append("- " + " | ".join(segments))
    return lines


def _format_dialogue_runtime_task(task: Any) -> str:
    if not isinstance(task, dict):
        return f"- {task}"
    task_type = task.get("task_type") or "task"
    status = task.get("status") or "unknown"
    task_id = task.get("task_id") or "-"
    tool_name = task.get("tool_name")
    suffix = f", tool={tool_name}" if tool_name else ""
    return f"- {task_type}({status}) id={task_id}{suffix}"


def _write_turn_artifacts(run_dir: Path, output: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    dialogue_path = run_dir / "dialogue.md"
    dialogue_path.write_text(_render_dialogue_markdown(output), encoding="utf-8")
    output["dialogue_path"] = str(dialogue_path)

    for turn in output.get("turns") or []:
        if not isinstance(turn, dict):
            continue
        turn_no = int(turn.get("turn") or 0)
        if turn_no <= 0:
            continue
        review_path = run_dir / f"turn{turn_no}_review.md"
        review_path.write_text(_render_turn_review_markdown(turn), encoding="utf-8")

        first_token_capture = turn.get("first_token_prompt_capture")
        if isinstance(first_token_capture, dict):
            first_token_path = run_dir / f"turn{turn_no}_first_token_FULL_PROMPT.md"
            first_token_path.write_text(
                _render_prompt_capture_markdown("First Token Prompt", first_token_capture),
                encoding="utf-8",
            )

        for capture in turn.get("prompt_captures") or []:
            if not isinstance(capture, dict):
                continue
            call_no = int(capture.get("call") or 0)
            if call_no <= 0:
                continue
            prompt_path = run_dir / f"turn{turn_no}_call{call_no}_FULL_PROMPT.md"
            prompt_path.write_text(
                _render_prompt_capture_markdown(f"Call {call_no}", capture),
                encoding="utf-8",
            )


async def _image_jobs(container, tenant_key: str) -> list[dict[str, Any]]:
    async with container.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT job_id, session_key, image_id, image_ref, focus, status,
                       external_job_id, last_error, created_at, updated_at,
                       started_at, completed_at
                FROM nb_image_analysis_jobs
                WHERE tenant_key = %s
                ORDER BY created_at DESC
                LIMIT 100
                """,
                (tenant_key,),
            )
            rows = await cur.fetchall()
    return [
        {
            "job_id": row[0],
            "session_key": row[1],
            "image_id": row[2],
            "image_ref": row[3],
            "focus": row[4],
            "status": row[5],
            "external_job_id": row[6],
            "last_error": row[7],
            "created_at": str(row[8]) if row[8] else None,
            "updated_at": str(row[9]) if row[9] else None,
            "started_at": str(row[10]) if row[10] else None,
            "completed_at": str(row[11]) if row[11] else None,
        }
        for row in rows
    ]


async def _cron_jobs(container, tenant_key: str) -> list[dict[str, Any]]:
    async with container.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, session_key, cron_type, cron_expr, interval_s,
                       run_at, task, status, last_run_at, created_at, updated_at
                FROM nb_cron_jobs
                WHERE tenant_key = %s
                ORDER BY created_at ASC
                LIMIT 100
                """,
                (tenant_key,),
            )
            rows = await cur.fetchall()
    return [
        {
            "id": row[0],
            "session_key": row[1],
            "cron_type": row[2],
            "cron_expr": row[3],
            "interval_s": row[4],
            "run_at": str(row[5]) if row[5] else None,
            "task": row[6],
            "status": row[7],
            "last_run_at": str(row[8]) if row[8] else None,
            "created_at": str(row[9]) if row[9] else None,
            "updated_at": str(row[10]) if row[10] else None,
        }
        for row in rows
    ]


async def _skin_diary_results(container, tenant_key: str) -> list[dict[str, Any]]:
    async with container.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, state, summary, analyzed_at, create_time, creator
                FROM nb_skin_diary_results
                WHERE tenant_key = %s AND deleted = 0
                ORDER BY COALESCE(create_time, analyzed_at) DESC
                LIMIT 100
                """,
                (tenant_key,),
            )
            rows = await cur.fetchall()
    return [
        {
            "id": row[0],
            "state": row[1],
            "summary": row[2],
            "analyzed_at": str(row[3]) if row[3] else None,
            "create_time": str(row[4]) if row[4] else None,
            "creator": row[5],
        }
        for row in rows
    ]


async def _session_messages(container, tenant_key: str, session_key: str) -> dict[str, Any]:
    async with container.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT seq, role, tool_name, message_json, created_at
                FROM nb_session_messages
                WHERE tenant_key = %s AND session_key = %s
                ORDER BY seq ASC
                """,
                (tenant_key, session_key),
            )
            rows = await cur.fetchall()

    messages = []
    for row in rows[-20:]:
        raw_message = row[3]
        parsed = None
        if raw_message:
            try:
                parsed = json.loads(raw_message) if isinstance(raw_message, str) else raw_message
            except Exception:
                parsed = None
        content = parsed.get("content") if isinstance(parsed, dict) else None
        messages.append(
            {
                "seq": row[0],
                "role": row[1],
                "tool_name": row[2],
                "content": content if isinstance(content, str) else "",
                "created_at": str(row[4]) if row[4] else None,
            }
        )

    return {
        "message_count": len(rows),
        "last_seq": rows[-1][0] if rows else None,
        "recent_messages": messages,
    }


async def _shutdown_container(container) -> None:
    listener_registry = getattr(container, "_scenario_event_listeners", None) or {}
    for state in listener_registry.values():
        task = state.get("task")
        if task is not None:
            task.cancel()
    if listener_registry:
        await asyncio.gather(
            *[state.get("task") for state in listener_registry.values() if state.get("task") is not None],
            return_exceptions=True,
        )
    for task in getattr(container, "worker_tasks", []) or []:
        task.cancel()
    if getattr(container, "worker_tasks", None):
        await asyncio.gather(*container.worker_tasks, return_exceptions=True)
    await container.db.close()


def _default_run_dir(scenario_id: str = "") -> Path:
    # 时间戳精确到毫秒并带上 scenario id，避免并行启动的 runner 写进同一目录
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    suffix = f"_{_sanitize_lock_token(scenario_id)}" if scenario_id else ""
    return Path("script/logs") / f"run_{stamp}{suffix}"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _default_max_seconds() -> float:
    raw = str(os.getenv("SCENARIO_RUNNER_MAX_SECONDS") or "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return 1800.0


async def _run_scenario_with_timeout(
    scenario: Path,
    *,
    run_dir: Path | None,
    max_seconds: float,
) -> dict[str, Any]:
    coro = run_scenario(scenario, run_dir=run_dir)
    if max_seconds <= 0:
        return await coro
    return await asyncio.wait_for(coro, timeout=max_seconds)


def _sanitize_lock_token(token: str) -> str:
    """把 scenario 标识收敛成可安全用于文件名的形式。"""
    cleaned = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(token or "").strip())
    return cleaned or "default"


def _scenario_runner_pids(scenario_token: str) -> list[int]:
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return []
    current_pid = os.getpid()
    pids: list[int] = []
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == current_pid:
            continue
        if (
            "script.runner.runner" in command
            and "--scenario" in command
            and scenario_token in command
        ):
            pids.append(pid)
    return pids


def _kill_stale_scenario_runners(scenario_token: str) -> list[int]:
    # 只清理跑同一个 scenario 的进程，避免误杀并行中的其它脚本
    pids = _scenario_runner_pids(scenario_token)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    deadline = time.monotonic() + 5
    remaining = list(pids)
    while remaining and time.monotonic() < deadline:
        alive: list[int] = []
        for pid in remaining:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            alive.append(pid)
        remaining = alive
        if remaining:
            time.sleep(0.2)
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
    return pids


@contextmanager
def _runner_process_lock(*, kill_stale: bool, scenario_token: str):
    if kill_stale:
        killed = _kill_stale_scenario_runners(scenario_token)
        if killed:
            print(f"killed stale scenario runner pids={killed}", file=sys.stderr)

    # 锁按 scenario 粒度：同一脚本防重复跑，不同脚本可并行
    lock_name = f".scenario_runner.{_sanitize_lock_token(scenario_token)}.lock"
    lock_path = ROOT / "script" / "logs" / lock_name
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+", encoding="utf-8")
    try:
        if fcntl is not None:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SystemExit(
                    f"another runner for scenario {scenario_token!r} is already active; "
                    "stop it first or rerun with --kill-stale"
                ) from exc
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(json.dumps({
            "pid": os.getpid(),
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False))
        lock_file.write("\n")
        lock_file.flush()
        yield
    finally:
        try:
            lock_file.seek(0)
            lock_file.truncate()
            lock_file.flush()
        except Exception:
            pass
        if fcntl is not None:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        lock_file.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one automation scenario")
    parser.add_argument("--scenario", required=True, help="Path to scenario YAML")
    parser.add_argument("--run-dir", default=None, help="Output directory")
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=_default_max_seconds(),
        help="Maximum scenario runtime before forced shutdown; use 0 to disable",
    )
    parser.add_argument(
        "--kill-stale",
        action="store_true",
        help="Terminate other local script.runner.runner processes before starting",
    )
    args = parser.parse_args()

    start = time.perf_counter()
    scenario_token = Path(args.scenario).stem
    with _runner_process_lock(kill_stale=bool(args.kill_stale), scenario_token=scenario_token):
        try:
            result = asyncio.run(
                _run_scenario_with_timeout(
                    Path(args.scenario),
                    run_dir=Path(args.run_dir) if args.run_dir else None,
                    max_seconds=float(args.max_seconds or 0),
                )
            )
        except TimeoutError as exc:
            elapsed = time.perf_counter() - start
            print(
                f"scenario timed out after {elapsed:.1f}s "
                f"(max_seconds={float(args.max_seconds or 0):.1f})",
                file=sys.stderr,
            )
            raise SystemExit(124) from exc
    print(f"verdict={result['verdict']} elapsed={time.perf_counter() - start:.1f}s")
    if result["verdict"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
