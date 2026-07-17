"""Cron 相关的共享时间工具。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

BEIJING_TZ = timezone(timedelta(hours=8))


def now_local() -> datetime:
    """返回北京时间的 naive datetime。"""
    return datetime.now(BEIJING_TZ).replace(tzinfo=None)


def now_local_str() -> str:
    """返回北京时间字符串，供 MySQL DATETIME 写入。"""
    return now_local().strftime("%Y-%m-%d %H:%M:%S")


def dt_str(dt: datetime) -> str:
    """把 naive datetime 序列化为 MySQL DATETIME 字符串。"""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_datetime(s: str) -> datetime | None:
    """尝试解析 ISO 8601 风格的日期时间字符串。"""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def next_cron_run(cron_expr: str) -> datetime | None:
    """计算 cron 表达式对应的下次执行时间。"""
    try:
        from croniter import croniter

        return croniter(cron_expr, now_local()).get_next(datetime)
    except Exception:
        return None
