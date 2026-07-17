"""Shared weather query helpers for tools and background generation."""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger


FOCUSES = {"general", "outing", "skincare"}
TIME_SCOPES = {"today", "tomorrow", "next_days"}
_MISSING_VALUES = {None, "", "暂无", 999999, "999999"}


class BaiduWeatherService:
    """Small Baidu weather client returning model-ready summaries."""

    def __init__(
        self,
        *,
        api_url: str,
        ak: str,
        timeout_s: float = 3.0,
        transport: Any | None = None,
    ) -> None:
        self._api_url = str(api_url or "").strip().rstrip("/") + "/"
        self._ak = str(ak or "").strip()
        self._timeout_s = max(1.0, float(timeout_s or 3.0))
        self._transport = transport

    async def query(self, *, location: str, focus: str = "general", time_scope: str = "today") -> dict[str, Any]:
        location = str(location or "").strip()
        focus = _normalize_enum(focus, FOCUSES, "general")
        time_scope = _normalize_enum(time_scope, TIME_SCOPES, "today")
        if not location:
            return _failure_payload("missing_location", "缺少地区。", "先问用户在哪个城市或区县。")
        if not self._ak:
            return _failure_payload(
                "missing_api_key",
                "天气查询工具缺少 BAIDU_MAP_AK 配置。",
                "天气查询还没配置好。请自然告诉用户这次暂时查不了实时天气。",
            )

        params: dict[str, Any] = {"ak": self._ak, "data_type": "all", "output": "json"}
        if _looks_like_lng_lat(location):
            params["location"] = location
            params["coordtype"] = "wgs84"
        else:
            params["district"] = location

        payload = await self._request(params)
        if not payload.get("ok", True):
            return payload

        if int(payload.get("status", -1)) != 0:
            message = str(payload.get("message") or payload.get("msg") or "").strip()
            return _failure_payload(
                "weather_error",
                f"天气查询失败：{message}" if message else "天气查询失败。",
                "天气查询没有成功。请用自然口吻告诉用户，不要暴露技术细节。",
            )

        result = payload.get("result") or {}
        if not isinstance(result, dict):
            return _failure_payload("empty_result", "天气查询没有返回有效结果。", "这次没有查到有效天气结果。")

        return {
            "ok": True,
            "action": "queried",
            "provider": "baidu_map_weather",
            "location_query": location,
            "time_scope": time_scope,
            "user_visible_summary": build_weather_summary(result, focus=focus, time_scope=time_scope),
            "message_focus": (
                "天气查好了，基于 user_visible_summary 简短回答。"
                "下雨就提醒带伞；天气炎热或紫外线强时提醒防晒。"
                "只有用户明确追问防晒产品、手边产品怎么用、或问护肤柜里有没有合适产品时，才调用护肤柜工具。"
                "不要因为查到天气就触发肌肤日记、深度报告，或把“今日护肤计划”改成护肤品收集流程。"
            ),
        }

    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            import httpx
        except ImportError:
            return _failure_payload(
                "dependency_missing",
                "天气查询工具缺少 httpx 依赖。",
                "天气查询工具缺少运行依赖。请告诉用户这次没查成。",
            )

        logger.info("query_weather GET {} params={}", self._api_url, _safe_params(params))
        try:
            client_kwargs: dict[str, Any] = {"timeout": self._timeout_s}
            if self._transport is not None:
                client_kwargs["transport"] = self._transport
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.get(self._api_url, params=params)
                response.raise_for_status()
        except httpx.TimeoutException:
            return _failure_payload("timeout", "天气查询请求超时。", "天气查询超时了，请让用户稍后再试。")
        except httpx.HTTPStatusError as exc:
            return _failure_payload(
                "http_error",
                f"天气查询服务返回 HTTP {exc.response.status_code}。",
                "天气服务返回错误。请告诉用户这次没查成。",
            )
        except httpx.RequestError as exc:
            logger.warning("query_weather request failed: {}", exc)
            return _failure_payload(
                "request_error",
                "无法连接到天气查询服务。",
                "这次没连上天气查询服务。请自然告诉用户稍后再试。",
            )

        try:
            return response.json()
        except json.JSONDecodeError:
            return _failure_payload(
                "invalid_response",
                "天气查询服务响应不是有效 JSON。",
                "天气服务返回内容异常。请告诉用户这次没有确认查询成功。",
            )


def extract_city_from_user_profile(user_profile: str) -> str:
    """Conservatively extract a current city from USER.md-like text."""

    for raw_line in str(user_profile or "").splitlines():
        line = raw_line.strip().lstrip("-*•").strip()
        if not line:
            continue
        for pattern in (
            r"(?:当前|所在|常驻|居住|生活)?城市\s*[：:]\s*([一-龥]{2,12}市?)",
            r"(?:所在地|所在地区|当前位置|常驻地|居住地)\s*[：:]\s*([一-龥]{2,12}市?)",
            r"(?:今天|现在|目前|当前)(?:在|位于)\s*([一-龥]{2,12}市?)",
        ):
            match = re.search(pattern, line)
            if not match:
                continue
            city = _clean_city(match.group(1))
            if city:
                return city
    return ""


def build_weather_summary(result: dict[str, Any], *, focus: str, time_scope: str) -> str:
    location = result.get("location") or result.get("address") or {}
    place = _place_name(location)
    now = _clean_weather_obj(result.get("now"))
    forecasts = _forecasts_for_scope(result.get("forecasts"), time_scope=time_scope)
    indexes = _selected_indexes(result.get("indexes"))

    parts: list[str] = []
    if now and time_scope in {"today", "next_days"}:
        parts.append(_now_summary(place, now))
    if forecasts:
        label = "未来几天" if time_scope == "next_days" else ("明天" if time_scope == "tomorrow" else "今天")
        parts.append(f"{label}预报：" + "；".join(_forecast_summary(item) for item in forecasts))
    index_line = _index_summary(indexes)
    if index_line:
        parts.append("指数：" + index_line)

    tips = _tips(now=now, forecasts=forecasts, indexes=indexes, focus=focus)
    if tips:
        parts.append("建议：" + "；".join(tips[:4]))
    return "\n".join(part for part in parts if part)


def _failure_payload(status: str, error: str, message_focus: str) -> dict[str, Any]:
    return {"ok": False, "status": status, "error": error, "message_focus": message_focus}


def _normalize_enum(value: str, allowed: set[str], default: str) -> str:
    normalized = str(value or default).strip().lower()
    return normalized if normalized in allowed else default


def _looks_like_lng_lat(value: str) -> bool:
    return bool(re.fullmatch(r"\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*", value or ""))


def _safe_params(params: dict[str, Any]) -> dict[str, Any]:
    safe = dict(params)
    if safe.get("ak"):
        safe["ak"] = "***"
    return safe


def _clean_city(value: str) -> str:
    city = str(value or "").strip().strip("，,。；;、 ")
    if not city or any(token in city for token in ("天气", "护肤", "防晒", "产品")):
        return ""
    return city


def _clean_weather_obj(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(k): cleaned for k, v in value.items() if (cleaned := _clean_value(v)) is not None}


def _clean_weather_list(value: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value[: max(0, int(limit))]:
        cleaned = _clean_weather_obj(item)
        if cleaned:
            out.append(cleaned)
    return out


def _clean_value(value: Any) -> Any | None:
    if value in _MISSING_VALUES:
        return None
    return value


def _selected_indexes(value: Any) -> list[dict[str, Any]]:
    indexes = _clean_weather_list(value, limit=12)
    wanted = {"紫外线指数", "穿衣指数", "运动指数"}
    return [item for item in indexes if str(item.get("name") or "").strip() in wanted][:4]


def _forecasts_for_scope(value: Any, *, time_scope: str) -> list[dict[str, Any]]:
    forecasts = _clean_weather_list(value, limit=3)
    if time_scope == "next_days":
        return forecasts
    if time_scope == "tomorrow":
        return forecasts[1:2] if len(forecasts) > 1 else forecasts[:1]
    return forecasts[:1]


def _place_name(location: Any) -> str:
    if not isinstance(location, dict):
        return "当地"
    values = [
        str(location.get("province") or "").strip(),
        str(location.get("city") or "").strip(),
        str(location.get("name") or location.get("district") or "").strip(),
    ]
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return "".join(deduped) or "当地"


def _now_summary(place: str, now: dict[str, Any]) -> str:
    bits = [f"{place}当前{now.get('text')}" if now.get("text") else f"{place}当前天气"]
    if now.get("temp") is not None:
        bits.append(f"{now.get('temp')}℃")
    if now.get("feels_like") is not None:
        bits.append(f"体感{now.get('feels_like')}℃")
    if now.get("rh") is not None:
        bits.append(f"湿度{now.get('rh')}%")
    wind = "".join(str(now.get(k) or "") for k in ("wind_dir", "wind_class")).strip()
    if wind:
        bits.append(wind)
    if now.get("aqi") is not None:
        bits.append(f"AQI {now.get('aqi')}")
    return "，".join(bits) + "。"


def _forecast_summary(item: dict[str, Any]) -> str:
    date = str(item.get("date") or "").strip()
    day = str(item.get("text_day") or "").strip()
    night = str(item.get("text_night") or "").strip()
    temp = ""
    if item.get("low") is not None and item.get("high") is not None:
        temp = f"{item.get('low')}~{item.get('high')}℃"
    weather = day if day == night or not night else f"{day}转{night}"
    return " ".join(part for part in (date, weather, temp) if part)


def _index_summary(indexes: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in indexes:
        name = str(item.get("name") or "").strip()
        brief = str(item.get("brief") or "").strip()
        if name and brief:
            lines.append(f"{name}{brief}")
    return "；".join(lines)


def _tips(
    *,
    now: dict[str, Any],
    forecasts: list[dict[str, Any]],
    indexes: list[dict[str, Any]],
    focus: str,
) -> list[str]:
    tips: list[str] = []
    weather_text = " ".join(
        str(value)
        for value in [
            now.get("text"),
            *(item.get("text_day") for item in forecasts),
            *(item.get("text_night") for item in forecasts),
        ]
        if value
    )
    if focus in {"general", "outing"} and any(token in weather_text for token in ("雨", "雪", "雷")):
        tips.append("出门记得带伞。")

    if focus in {"outing", "skincare"}:
        rh = _to_int(now.get("rh"))
        if rh is not None and rh < 40:
            tips.append("湿度偏低，护肤可以偏保湿一点。")
        elif rh is not None and rh >= 75:
            tips.append("湿度偏高，白天别叠太厚重。")
        uv = next((item for item in indexes if str(item.get("name") or "") == "紫外线指数"), None)
        if uv:
            tips.append("户外时间长就按紫外线情况补涂防晒。")
    return tips


def _to_int(value: Any) -> int | None:
    try:
        if value in _MISSING_VALUES:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
