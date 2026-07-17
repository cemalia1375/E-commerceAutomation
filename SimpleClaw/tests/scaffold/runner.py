"""场景测试脚手架入口。

用法：
    cd SimpleClaw

    # 用自动隔离的测试用户跑所有场景
    python -m tests.scaffold.runner

    # 用真实用户数据跑（上下文更真实）
    python -m tests.scaffold.runner --user-id real_user_123

    # 只跑指定场景
    python -m tests.scaffold.runner --scenarios S01,S03

    # 调整后台任务等待时间（秒，默认 8）
    python -m tests.scaffold.runner --wait-bg 12

    # 跑完清理测试数据（仅对自动生成的 test_ 用户有效）
    python -m tests.scaffold.runner --cleanup
"""

from __future__ import annotations

import argparse
import asyncio
import time

from tests.scaffold.client import ScaffoldClient
from tests.scaffold.scenarios import ALL_SCENARIOS, ScenarioResult

# ANSI 颜色
_GREEN  = "\033[92m"
_RED    = "\033[91m"
_YELLOW = "\033[93m"
_CYAN   = "\033[96m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"

def _ok(s: str)   -> str: return f"{_GREEN}✓{_RESET} {s}"
def _fail(s: str) -> str: return f"{_RED}✗{_RESET} {s}"
def _info(s: str) -> str: return f"{_CYAN}·{_RESET} {s}"
def _head(s: str) -> str: return f"\n{_BOLD}{s}{_RESET}"


def _print_result(sr: ScenarioResult) -> None:
    status = f"{_GREEN}PASS{_RESET}" if sr.passed else f"{_RED}FAIL{_RESET}"
    print(f"{_BOLD}[{status}{_BOLD}]{_RESET} {sr.scenario}")
    for a in sr.asserts:
        if a.passed:
            msg = _ok(a.name)
        else:
            msg = _fail(a.name)
        detail = f"  {_YELLOW}→ {a.detail}{_RESET}" if a.detail else ""
        print(f"    {msg}{detail}")


async def run_all(
    base_url: str,
    user_id: str,
    wait_bg: float,
    filter_names: list[str] | None,
) -> list[ScenarioResult]:
    client = ScaffoldClient(base_url=base_url)

    # 连通性检查
    if not await client.health():
        print(f"{_RED}ERROR:{_RESET} 无法连接到 {base_url}，请先启动服务（./dev.sh）")
        return []

    # 过滤场景
    scenarios = ALL_SCENARIOS
    if filter_names:
        fl = [f.upper() for f in filter_names]
        scenarios = [s for s in scenarios if any(s.name.upper().startswith(f) for f in fl)]
        if not scenarios:
            print(f"{_YELLOW}WARNING:{_RESET} 没有匹配的场景：{filter_names}")
            return []

    print(_head(f"Mojing 场景脚手架  user_id={user_id}  server={base_url}"))
    print(_info(f"共 {len(scenarios)} 个场景，后台任务等待 {wait_bg}s"))

    results: list[ScenarioResult] = []
    total_start = time.perf_counter()

    for ScenCls in scenarios:
        scenario = ScenCls(client, user_id, wait_bg=wait_bg)
        print(_head(f"▶ {scenario.name}  — {scenario.description}"))
        t0 = time.perf_counter()
        try:
            sr = await scenario.run()
        except Exception as exc:
            sr = type("_SR", (), {
                "scenario": scenario.name,
                "passed": False,
                "failed_count": 1,
                "asserts": [],
            })()
            sr.asserts = [type("_A", (), {"name": "场景执行", "passed": False,
                                          "detail": str(exc)})()]
        elapsed = time.perf_counter() - t0
        _print_result(sr)
        print(_info(f"耗时 {elapsed:.1f}s"))
        results.append(sr)

    # 汇总
    total = time.perf_counter() - total_start
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    total_asserts = sum(len(r.asserts) for r in results)
    failed_asserts = sum(r.failed_count for r in results)

    print(_head("─" * 50))
    print(f"场景：{_GREEN}{passed} 通过{_RESET}  {_RED}{failed} 失败{_RESET}  "
          f"断言：{total_asserts - failed_asserts}/{total_asserts}  "
          f"总耗时 {total:.1f}s")

    return results


async def cleanup(base_url: str, user_id: str) -> None:
    """清理测试用户数据（仅对 test_ 前缀用户执行）。"""
    if not user_id.startswith("test_"):
        print(_info(f"user_id={user_id!r} 非 test_ 前缀，跳过清理"))
        return
    print(_info(f"清理测试数据 user_id={user_id}（需要 DB 直连，当前暂跳过）"))
    # TODO: 直连 DB 删除 nb_session_messages / nb_agent_obligations / nb_tenant_documents 中 tenant_key=user_id 的行


def main() -> None:
    parser = argparse.ArgumentParser(description="Mojing 场景脚手架")
    parser.add_argument("--base-url",  default="http://localhost:8000", help="服务地址")
    parser.add_argument("--user-id",   default=None, help="测试用 tenant_key（默认自动生成 test_scaffold_xxx）")
    parser.add_argument("--wait-bg",   type=float, default=8.0, help="等待后台任务的秒数（默认 8）")
    parser.add_argument("--scenarios", default=None, help="逗号分隔的场景前缀，如 S01,S03")
    parser.add_argument("--cleanup",   action="store_true", help="跑完后清理测试数据")
    args = parser.parse_args()

    # 自动生成隔离用户 or 使用指定用户
    if args.user_id:
        user_id = args.user_id
        print(_info(f"使用指定用户 {user_id!r}（真实数据模式）"))
    else:
        ts = int(time.time())
        user_id = f"test_scaffold_{ts}"
        print(_info(f"自动生成隔离用户 {user_id!r}"))

    filter_names = [s.strip() for s in args.scenarios.split(",")] if args.scenarios else None

    results = asyncio.run(run_all(
        base_url=args.base_url,
        user_id=user_id,
        wait_bg=args.wait_bg,
        filter_names=filter_names,
    ))

    if args.cleanup and results:
        asyncio.run(cleanup(args.base_url, user_id))

    # 非零退出码方便 CI
    if results and any(not r.passed for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
