from __future__ import annotations

import json
import unittest

import httpx

from Mojing.services.weather import extract_city_from_user_profile
from Mojing.tools.weather import QueryWeatherTool


class WeatherToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_api_key_returns_user_visible_failure(self) -> None:
        tool = QueryWeatherTool(api_url="https://api.map.baidu.com/weather/v1/", ak="")

        result = await tool.execute(location="北京市")
        payload = json.loads(result.content)

        self.assertFalse(result.ok)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "missing_api_key")
        self.assertIn("BAIDU_MAP_AK", payload["error"])

    async def test_location_query_returns_compact_weather_payload(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=_sample_payload())

        tool = QueryWeatherTool(
            api_url="https://api.map.baidu.com/weather/v1/",
            ak="test-ak",
            transport=httpx.MockTransport(handler),
        )

        params = tool.cast_params({"location": "北京市", "focus": "outing", "city": "ignored"})
        result = await tool.execute(**params)
        payload = json.loads(result.content)

        self.assertTrue(result.ok)
        self.assertEqual(payload["location_query"], "北京市")
        self.assertEqual(payload["time_scope"], "today")
        self.assertNotIn("query", payload)
        self.assertNotIn("now", payload)
        self.assertNotIn("forecasts", payload)
        self.assertNotIn("indexes", payload)
        self.assertIn("北京市东城", payload["user_visible_summary"])
        self.assertIn("今天预报", payload["user_visible_summary"])
        self.assertNotIn("2026-06-02", payload["user_visible_summary"])
        self.assertNotIn("sunscreen_products", payload)
        self.assertIn("明确追问防晒产品", payload["message_focus"])
        self.assertIn("今日护肤计划", payload["message_focus"])
        self.assertEqual(requests[0].url.params.get("ak"), "test-ak")
        self.assertEqual(requests[0].url.params.get("district"), "北京市")

    async def test_skincare_focus_keeps_weather_only_guidance(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(200, json=_sample_payload())

        tool = QueryWeatherTool(
            api_url="https://api.map.baidu.com/weather/v1/",
            ak="test-ak",
            transport=httpx.MockTransport(handler),
        )

        result = await tool.execute(location="北京市", focus="skincare")
        payload = json.loads(result.content)

        self.assertTrue(result.ok)
        self.assertIn("湿度偏低", payload["user_visible_summary"])
        self.assertIn("防晒", payload["message_focus"])

    async def test_next_days_scope_includes_multiple_forecast_days(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(200, json=_sample_payload())

        tool = QueryWeatherTool(
            api_url="https://api.map.baidu.com/weather/v1/",
            ak="test-ak",
            transport=httpx.MockTransport(handler),
        )

        result = await tool.execute(location="北京市", time_scope="next_days")
        payload = json.loads(result.content)

        self.assertTrue(result.ok)
        self.assertEqual(payload["time_scope"], "next_days")
        self.assertIn("未来几天预报", payload["user_visible_summary"])
        self.assertIn("2026-06-01", payload["user_visible_summary"])
        self.assertIn("2026-06-02", payload["user_visible_summary"])

    async def test_baidu_error_is_normalized(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(200, json={"status": 401, "message": "ak不合法"})

        tool = QueryWeatherTool(
            api_url="https://api.map.baidu.com/weather/v1/",
            ak="bad-ak",
            transport=httpx.MockTransport(handler),
        )

        result = await tool.execute(location="北京市")
        payload = json.loads(result.content)

        self.assertFalse(result.ok)
        self.assertEqual(payload["status"], "weather_error")
        self.assertIn("ak不合法", payload["error"])

    async def test_extract_city_from_user_profile_is_conservative(self) -> None:
        self.assertEqual(extract_city_from_user_profile("当前城市：广州\n"), "广州")
        self.assertEqual(extract_city_from_user_profile("- 今天在深圳\n"), "深圳")
        self.assertEqual(extract_city_from_user_profile("我喜欢广州的天气"), "")


def _sample_payload() -> dict:
    return {
        "status": 0,
        "message": "success",
        "result": {
            "location": {
                "country": "中国",
                "province": "北京市",
                "city": "北京市",
                "name": "东城",
                "id": "110101",
            },
            "now": {
                "temp": 28,
                "feels_like": 30,
                "rh": 35,
                "wind_class": "2级",
                "wind_dir": "东风",
                "text": "晴",
                "prec_1h": 0,
                "aqi": 80,
                "uptime": "20260601100000",
            },
            "indexes": [
                {
                    "name": "紫外线指数",
                    "brief": "强",
                    "detail": "紫外线较强，注意防晒。",
                }
            ],
            "forecasts": [
                {
                    "date": "2026-06-01",
                    "week": "星期一",
                    "high": 31,
                    "low": 22,
                    "text_day": "晴",
                    "text_night": "多云",
                },
                {
                    "date": "2026-06-02",
                    "week": "星期二",
                    "high": 32,
                    "low": 23,
                    "text_day": "多云",
                    "text_night": "阵雨",
                }
            ],
        },
    }
