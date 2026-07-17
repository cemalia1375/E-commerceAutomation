"""Hard assertion and soft check evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


def evaluate_checks(checks: list[Any] | None, turn: dict[str, Any]) -> list[CheckResult]:
    results: list[CheckResult] = []
    for item in checks or []:
        if isinstance(item, str):
            results.append(_eval_named(item, None, turn))
        elif isinstance(item, dict):
            for name, expected in item.items():
                results.append(_eval_named(str(name), expected, turn))
        else:
            results.append(CheckResult(str(item), False, "unsupported check format"))
    return results


def _eval_named(name: str, expected: Any, turn: dict[str, Any]) -> CheckResult:
    reply = str(turn.get("reply") or "")
    first_token_reply = str(turn.get("first_token_reply") or "")
    main_reply = str(turn.get("main_reply") or "")
    tools_called = list(turn.get("tools_called") or [])

    if name == "reply_non_empty":
        passed = bool(reply.strip()) is bool(expected)
        return CheckResult(_fmt(name, expected), passed, f"reply_len={len(reply)}")

    if name == "first_token_non_empty":
        passed = bool(first_token_reply.strip()) is bool(expected)
        return CheckResult(_fmt(name, expected), passed, f"first_token_len={len(first_token_reply)}")

    if name == "main_reply_non_empty":
        passed = bool(main_reply.strip()) is bool(expected)
        return CheckResult(_fmt(name, expected), passed, f"main_reply_len={len(main_reply)}")

    if name == "not_tool_called":
        if expected == "*":
            passed = not tools_called
        else:
            passed = expected not in tools_called
        return CheckResult(_fmt(name, expected), passed, f"tools_called={tools_called}")

    if name == "tool_called":
        passed = expected in tools_called
        return CheckResult(_fmt(name, expected), passed, f"tools_called={tools_called}")

    if name == "tool_called_count":
        expected_tool, expected_count = _parse_tool_count(expected)
        if not expected_tool:
            return CheckResult(_fmt(name, expected), False, "expected must include tool")
        actual_count = sum(1 for tool in tools_called if tool == expected_tool)
        passed = actual_count == expected_count
        return CheckResult(
            _fmt(name, expected),
            passed,
            f"{expected_tool}_count={actual_count} tools_called={tools_called}",
        )

    if name == "tool_result_route":
        expected_tool, expected_route = _parse_tool_result_route(expected)
        tools = list(turn.get("tools") or [])
        matched = [
            tool for tool in tools
            if (not expected_tool or tool.get("tool_name") == expected_tool)
            and ((tool.get("result") or {}).get("route") == expected_route)
        ]
        return CheckResult(_fmt(name, expected), bool(matched), f"tools={tools}")

    if name == "tool_result_status":
        expected_tool, expected_status = _parse_tool_result_status(expected)
        tools = list(turn.get("tools") or [])
        matched = [
            tool for tool in tools
            if (not expected_tool or tool.get("tool_name") == expected_tool)
            and ((tool.get("result") or {}).get("status") == expected_status)
        ]
        return CheckResult(_fmt(name, expected), bool(matched), f"tools={tools}")

    if name == "tool_result_action":
        expected_tool, expected_action = _parse_tool_result_status(expected)
        tools = list(turn.get("tools") or [])
        matched = [
            tool for tool in tools
            if (not expected_tool or tool.get("tool_name") == expected_tool)
            and (
                tool.get("action") == expected_action
                or (tool.get("result") or {}).get("action") == expected_action
            )
        ]
        return CheckResult(_fmt(name, expected), bool(matched), f"tools={tools}")

    if name == "tool_result_reason":
        expected_tool, expected_reason = _parse_tool_result_status(expected)
        tools = list(turn.get("tools") or [])
        matched = [
            tool for tool in tools
            if (not expected_tool or tool.get("tool_name") == expected_tool)
            and ((tool.get("result") or {}).get("reason") == expected_reason)
        ]
        return CheckResult(_fmt(name, expected), bool(matched), f"tools={tools}")

    if name == "memory_entry_exists":
        spec = expected if isinstance(expected, dict) else {"topic_contains": str(expected)}
        entries = list(turn.get("memory_entries") or [])
        topic_sub = str(spec.get("topic_contains") or "")
        source = str(spec.get("source") or "")
        desc_sub = str(spec.get("description_contains") or "")
        skin_only = bool(spec.get("skin_only", False))
        matched = [
            e for e in entries
            if (not topic_sub or topic_sub in str(e.get("topic") or ""))
            and (not source or str(e.get("source") or "") == source)
            and (not desc_sub or desc_sub in str(e.get("description") or ""))
            and (not skin_only or bool(e.get("is_skin")))
        ]
        return CheckResult(_fmt(name, expected), bool(matched),
                           f"matched={len(matched)} of {len(entries)} entries")

    if name == "memory_entry_applied":
        spec = expected if isinstance(expected, dict) else {"topic_contains": str(expected)}
        topic_sub = str(spec.get("topic_contains") or "")
        arts = [a for a in (turn.get("dream_artifacts") or [])
                if topic_sub in str(a.get("content") or "")]
        passed = any(bool(a.get("applied")) for a in arts)
        return CheckResult(_fmt(name, expected), passed,
                           f"artifacts={len(arts)} applied={[a.get('applied') for a in arts]}")

    if name == "memory_entry_draft_only":
        spec = expected if isinstance(expected, dict) else {"topic_contains": str(expected)}
        topic_sub = str(spec.get("topic_contains") or "")
        arts = [a for a in (turn.get("dream_artifacts") or [])
                if topic_sub in str(a.get("content") or "")]
        passed = bool(arts) and all(not bool(a.get("applied")) for a in arts)
        return CheckResult(_fmt(name, expected), passed,
                           f"artifacts={len(arts)} applied={[a.get('applied') for a in arts]}")

    if name == "guardrail_verdict":
        want = str(expected or "")
        verdicts = [
            str((l.get("guardrail") or {}).get("verdict") or "")
            for l in (turn.get("memory_ledgers") or [])
            if isinstance(l.get("guardrail"), dict)
        ]
        actual = verdicts[-1] if verdicts else "absent"
        return CheckResult(_fmt(name, expected), actual == want, f"actual={actual}")

    if name == "reply_contains":
        passed = str(expected) in reply
        return CheckResult(_fmt(name, expected), passed)

    if name == "session_event_contains":
        events = list(turn.get("session_events") or [])
        expected_text = str(expected)
        serialized = "\n".join(str(item) for item in events)
        passed = expected_text in serialized
        return CheckResult(_fmt(name, expected), passed, f"event_count={len(events)}")

    if name == "reply_not_contains":
        passed = str(expected) not in reply
        return CheckResult(_fmt(name, expected), passed)

    if name == "first_token_not_contains":
        passed = str(expected) not in first_token_reply
        return CheckResult(_fmt(name, expected), passed)

    if name == "first_token_len_lt":
        try:
            limit = int(expected)
        except Exception:
            return CheckResult(_fmt(name, expected), False, "expected must be int")
        return CheckResult(_fmt(name, expected), len(first_token_reply) < limit, f"first_token_len={len(first_token_reply)}")

    if name == "reply_len_lt":
        try:
            limit = int(expected)
        except Exception:
            return CheckResult(_fmt(name, expected), False, "expected must be int")
        return CheckResult(_fmt(name, expected), len(reply) < limit, f"reply_len={len(reply)}")

    if name == "main_reply_len_lt":
        try:
            limit = int(expected)
        except Exception:
            return CheckResult(_fmt(name, expected), False, "expected must be int")
        return CheckResult(_fmt(name, expected), len(main_reply) < limit, f"main_reply_len={len(main_reply)}")

    if name == "ttft_ms_lt":
        ttft = turn.get("ttft_ms")
        if ttft is None:
            return CheckResult(_fmt(name, expected), False, "ttft_ms is null")
        return CheckResult(_fmt(name, expected), float(ttft) < float(expected), f"ttft_ms={ttft}")

    if name == "runtime_task_created":
        created = list(turn.get("runtime_tasks_created") or [])
        matched = [t for t in created if t.get("task_type") == expected]
        return CheckResult(_fmt(name, expected), bool(matched), f"created={created}")

    if name == "no_runtime_task_created":
        created = list(turn.get("runtime_tasks_created") or [])
        matched = [t for t in created if t.get("task_type") == expected]
        return CheckResult(_fmt(name, expected), not matched, f"created={created}")

    if name == "runtime_task_succeeded":
        created = list(turn.get("runtime_tasks_created") or [])
        matched = [
            t for t in created
            if t.get("task_type") == expected and t.get("status") == "succeeded"
        ]
        return CheckResult(_fmt(name, expected), bool(matched), f"created={created}")

    if name == "runtime_task_status":
        task_type, status = _parse_task_status(expected)
        if not task_type or not status:
            return CheckResult(_fmt(name, expected), False, "expected must include task_type and status")
        created = list(turn.get("runtime_tasks_created") or [])
        matched = [
            t for t in created
            if t.get("task_type") == task_type and t.get("status") == status
        ]
        return CheckResult(_fmt(name, expected), bool(matched), f"created={created}")

    if name == "image_job_created":
        created = list(turn.get("image_jobs_created") or [])
        if expected in (None, True, "*"):
            matched = created
        elif isinstance(expected, dict):
            status = str(expected.get("status") or "")
            focus = str(expected.get("focus") or "")
            matched = [
                job for job in created
                if (not status or job.get("status") == status)
                and (not focus or job.get("focus") == focus)
            ]
        else:
            matched = [job for job in created if job.get("status") == expected]
        return CheckResult(_fmt(name, expected), bool(matched), f"created={created}")

    if name == "image_job_status":
        status, source = _parse_image_job_status(expected)
        if not status:
            return CheckResult(_fmt(name, expected), False, "expected must include status")
        jobs = list(turn.get("image_jobs_created") if source == "created" else turn.get("image_jobs_after") or [])
        matched = [job for job in jobs if job.get("status") == status]
        return CheckResult(_fmt(name, expected), bool(matched), f"jobs={jobs}")

    if name == "cron_job_created":
        created = list(turn.get("cron_jobs_created") or [])
        matched = [
            job for job in created
            if expected in (None, "*") or job.get("cron_type") == expected
        ]
        return CheckResult(_fmt(name, expected), bool(matched), f"created={created}")

    if name == "cron_job_status":
        jobs = list(turn.get("cron_jobs_after") or [])
        matched = [job for job in jobs if job.get("status") == expected]
        return CheckResult(_fmt(name, expected), bool(matched), f"jobs={jobs}")

    if name == "cron_job_task_contains":
        jobs = list(turn.get("cron_jobs_after") or [])
        expected_text = str(expected)
        matched = [job for job in jobs if expected_text in str(job.get("task") or "")]
        return CheckResult(_fmt(name, expected), bool(matched), f"jobs={jobs}")

    if name == "cron_job_last_run_exists":
        jobs = list(turn.get("cron_jobs_after") or [])
        expected_bool = bool(expected)
        matched = [job for job in jobs if bool(job.get("last_run_at")) is expected_bool]
        return CheckResult(_fmt(name, expected), bool(matched), f"jobs={jobs}")

    if name == "session_messages_increased_by_at_least":
        delta = (turn.get("session_delta") or {}).get("message_count_delta")
        if delta is None:
            return CheckResult(_fmt(name, expected), False, "session_delta missing")
        return CheckResult(_fmt(name, expected), int(delta) >= int(expected), f"delta={delta}")

    if name == "subagent_session_messages_increased_by_at_least":
        agent, minimum = _parse_agent_count(expected)
        if not agent:
            return CheckResult(_fmt(name, expected), False, "expected must include agent")
        sub_delta = (turn.get("subagent_session_delta") or {}).get(agent) or {}
        delta = sub_delta.get("message_count_delta")
        if delta is None:
            return CheckResult(_fmt(name, expected), False, f"subagent_session_delta missing for {agent}")
        return CheckResult(_fmt(name, expected), int(delta) >= minimum, f"{agent}_delta={sub_delta}")

    if name == "session_assistant_after_contains":
        expected_text = str(expected)
        session = turn.get("session_after") or {}
        messages = list(session.get("recent_messages") or [])
        matched = [
            msg for msg in messages
            if msg.get("role") == "assistant" and expected_text in str(msg.get("content") or "")
        ]
        return CheckResult(_fmt(name, expected), bool(matched), f"recent_messages={messages}")

    if name == "subagent_session_assistant_after_contains":
        agent, expected_text = _parse_agent_text(expected)
        if not agent:
            return CheckResult(_fmt(name, expected), False, "expected must include agent")
        subagent_sessions = turn.get("subagent_sessions_after") or {}
        session = subagent_sessions.get(agent) or {}
        messages = list(session.get("recent_messages") or [])
        matched = [
            msg for msg in messages
            if msg.get("role") == "assistant" and expected_text in str(msg.get("content") or "")
        ]
        return CheckResult(_fmt(name, expected), bool(matched), f"{agent}_recent_messages={messages}")

    if name == "postprocess_user_md_changed":
        delta = turn.get("doc_delta") or {}
        docs = delta.get("docs") or {}
        user_md = docs.get("USER.md") or {}
        return CheckResult(name, bool(user_md.get("changed")), f"USER.md={user_md}")

    if name == "user_md_contains":
        docs_after = turn.get("docs_after") or {}
        user_md = ((docs_after.get("USER.md") or {}).get("content") or "")
        passed = str(expected) in user_md
        return CheckResult(_fmt(name, expected), passed, f"user_md_len={len(user_md)}")

    if name == "doc_contains":
        doc_name, expected_text = _parse_doc_contains(expected)
        if not doc_name:
            return CheckResult(_fmt(name, expected), False, "expected must include doc")
        docs_after = turn.get("docs_after") or {}
        content = ((docs_after.get(doc_name) or {}).get("content") or "")
        passed = expected_text in content
        return CheckResult(_fmt(name, expected), passed, f"{doc_name}_len={len(content)}")

    if name == "doc_not_contains":
        doc_name, expected_text = _parse_doc_contains(expected)
        if not doc_name:
            return CheckResult(_fmt(name, expected), False, "expected must include doc")
        docs_after = turn.get("docs_after") or {}
        content = ((docs_after.get(doc_name) or {}).get("content") or "")
        passed = expected_text not in content
        return CheckResult(_fmt(name, expected), passed, f"{doc_name}_len={len(content)}")

    if name == "skin_diary_result_created":
        created = list(turn.get("skin_diary_results_created") or [])
        if expected in (None, True, "*"):
            matched = created
        elif isinstance(expected, dict):
            state = str(expected.get("state") or "")
            summary_contains = str(expected.get("summary_contains") or "")
            matched = [
                item for item in created
                if (not state or item.get("state") == state)
                and (not summary_contains or summary_contains in str(item.get("summary") or ""))
            ]
        else:
            matched = [item for item in created if item.get("state") == expected]
        return CheckResult(_fmt(name, expected), bool(matched), f"created={created}")

    if name == "no_skin_diary_result_created":
        created = list(turn.get("skin_diary_results_created") or [])
        return CheckResult(_fmt(name, expected), not created, f"created={created}")

    if name == "skin_diary_result_count_increased_by_at_least":
        before = len(turn.get("skin_diary_results_before") or [])
        after = len(turn.get("skin_diary_results_after") or [])
        delta = after - before
        return CheckResult(_fmt(name, expected), delta >= int(expected), f"delta={delta}")

    return CheckResult(_fmt(name, expected), False, "check not implemented in MVP runner")


def _fmt(name: str, expected: Any) -> str:
    if expected is None:
        return name
    return f"{name}:{expected}"


def _parse_agent_count(expected: Any) -> tuple[str | None, int]:
    if isinstance(expected, dict):
        agent = expected.get("agent") or expected.get("name")
        count = expected.get("count", expected.get("min", 1))
        return (str(agent) if agent else None), int(count)
    text = str(expected or "")
    if ":" in text:
        agent, raw_count = text.split(":", 1)
        return agent.strip(), int(raw_count.strip())
    return text.strip() or None, 1


def _parse_tool_count(expected: Any) -> tuple[str | None, int]:
    if isinstance(expected, dict):
        tool = expected.get("tool") or expected.get("tool_name") or expected.get("name")
        count = expected.get("count", 1)
        return (str(tool) if tool else None), int(count)
    text = str(expected or "")
    if ":" in text:
        tool, raw_count = text.split(":", 1)
        return tool.strip() or None, int(raw_count.strip())
    return text.strip() or None, 1


def _parse_agent_text(expected: Any) -> tuple[str | None, str]:
    if isinstance(expected, dict):
        agent = expected.get("agent") or expected.get("name")
        text = str(expected.get("text") or expected.get("contains") or "")
        return (str(agent) if agent else None), text
    raw = str(expected or "")
    if ":" in raw:
        agent, text = raw.split(":", 1)
        return agent.strip(), text.strip()
    return None, raw


def _parse_task_status(expected: Any) -> tuple[str | None, str | None]:
    if isinstance(expected, dict):
        task_type = expected.get("task_type") or expected.get("type") or expected.get("name")
        status = expected.get("status")
        return (str(task_type) if task_type else None), (str(status) if status else None)
    text = str(expected or "")
    if ":" in text:
        task_type, status = text.split(":", 1)
        return task_type.strip(), status.strip()
    return None, None


def _parse_tool_result_route(expected: Any) -> tuple[str | None, str]:
    if isinstance(expected, dict):
        tool = expected.get("tool") or expected.get("tool_name") or expected.get("name")
        route = expected.get("route")
        return (str(tool) if tool else None), str(route or "")
    text = str(expected or "").strip()
    if ":" in text:
        tool, route = text.split(":", 1)
        return tool.strip() or None, route.strip()
    return None, text


def _parse_tool_result_status(expected: Any) -> tuple[str | None, str]:
    if isinstance(expected, dict):
        tool = expected.get("tool") or expected.get("tool_name") or expected.get("name")
        status = expected.get("status")
        return (str(tool) if tool else None), str(status or "")
    text = str(expected or "").strip()
    if ":" in text:
        tool, status = text.split(":", 1)
        return tool.strip() or None, status.strip()
    return None, text


def _parse_image_job_status(expected: Any) -> tuple[str | None, str]:
    if isinstance(expected, dict):
        status = expected.get("status")
        source = str(expected.get("source") or "after")
        return (str(status) if status else None), source
    text = str(expected or "").strip()
    return text or None, "after"


def _parse_doc_contains(expected: Any) -> tuple[str | None, str]:
    if isinstance(expected, dict):
        doc = expected.get("doc") or expected.get("name")
        text = str(expected.get("text") or expected.get("contains") or "")
        return (str(doc) if doc else None), text
    raw = str(expected or "")
    if ":" in raw:
        doc, text = raw.split(":", 1)
        return doc.strip(), text.strip()
    return None, raw
