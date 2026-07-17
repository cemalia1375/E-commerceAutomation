"""千川报表数据抓取器：通过 Playwright CDP 监听 XHR 响应，
提取直播间投放的视频物料列表。

策略：attach 到长驻 Chromium → navigate 到统一推广→创意 tab 页 →
NetworkRecorder 捕获 material/list-required 接口响应 →
过滤 DataSetKey=overall_roi_promotion_matrial_tab_video_live →
解析 rows → 返回标准化 row 列表。

不直接 POST API（规避 msToken/a_bogus 签名 + 风控）。

注意：MVP 只取第一页（10 条），完整 30 条需要驱动分页 UI，留待 v2。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from Flowcut.browser.client import BrowserClient
from Flowcut.browser.network import NetworkRecorder

logger = logging.getLogger(__name__)

# 监听的接口 URL 特征（material/list-required，pmc 路径）
_QC_MATERIAL_LIST_PATTERN = (
    r"qianchuan\.jinritemai\.com/ad/api/pmc/v1/uni-promotion/material/list-required"
)

# 入口页：编辑现有推广 → 创意 tab（前端自动 fire material/list-required）
# adId 是该账号下的具体广告 ID；不同账号 adId 不同，先从 env 读，fallback 硬编码。
# 生产化时应通过另一个接口先 list ads 再 iterate（v2 工作）。
def _build_entry_url() -> str:
    aavid = os.getenv("FLOWCUT_QC_ADVERTISER_ID", "1852831850913418")
    ad_id = os.getenv("FLOWCUT_QC_AD_ID", "1852918923016212")
    return (
        "https://qianchuan.jinritemai.com/uni-creation/overall-live"
        f"?aavid={aavid}&adId={ad_id}&anchor=creative&type=edit"
    )


# 目标 DataSetKey
_TARGET_DATASET_KEY = "overall_roi_promotion_matrial_tab_video_live"


def _default_cdp_url() -> str:
    return os.getenv("FLOWCUT_QC_CDP_URL", "http://127.0.0.1:9222")


def _val(field: Any) -> Any:
    """从千川 camelCase 响应里取 .value 字段。"""
    if isinstance(field, dict):
        return field.get("value")
    return None


def _parse_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """从 list-required 响应的单行 row 提取标准化字段。

    响应结构（camelCase）：
      row = {
        "dimensions": {
          "materialId": {"value": "7620650...", "valueStr": "..."},
          "roi2MaterialVideoName": {"value": "视频名.mp4", ...},
          "roi2MaterialUploadTime": {"value": "2026-05-20 13:56:21", ...},
          "roi2MaterialStatus": {"value": "1", "valueStr": "投放中"},
          "roi2MaterialVideoPlayInfo": {"value": "<json string>", ...}
        },
        "metrics": {
          "statCostForRoi2": {"value": 0, "valueStr": "0.00"},
          ...
        }
      }

    返回 None 表示该行缺少 material_id（脏数据），应跳过。
    """
    dims = row.get("dimensions") or row.get("Dimensions") or {}
    metrics = row.get("metrics") or row.get("Metrics") or {}

    material_id = _val(dims.get("materialId"))
    if not material_id:
        return None

    material_name = _val(dims.get("roi2MaterialVideoName")) or ""
    upload_time = _val(dims.get("roi2MaterialUploadTime"))
    status_field = dims.get("roi2MaterialStatus") or {}
    status_text = status_field.get("valueStr") if isinstance(status_field, dict) else None

    # play_info 是字符串化的 JSON
    play_info_raw = _val(dims.get("roi2MaterialVideoPlayInfo"))
    play_info: dict[str, Any] = {}
    if isinstance(play_info_raw, str) and play_info_raw.startswith("{"):
        try:
            play_info = json.loads(play_info_raw)
        except json.JSONDecodeError:
            play_info = {}

    def _num(field_name: str, *, as_int: bool = False) -> float | int | None:
        f = metrics.get(field_name)
        v = _val(f)
        if v is None:
            return None
        try:
            num = float(v)
            return int(num) if as_int else num
        except (TypeError, ValueError):
            return None

    return {
        "material_id": str(material_id),
        "material_name": str(material_name),
        "upload_time": str(upload_time) if upload_time else None,
        "status_text": status_text,
        "video_id": play_info.get("VideoId"),
        "aweme_item_id": play_info.get("AwemeItemId"),
        "cost": _num("statCostForRoi2"),
        "conversions": _num("totalPayOrderCountForRoi2", as_int=True),
        "gmv": _num("totalPayOrderGmvForRoi2"),
        "impressions": None,  # 该 DataSetKey 不提供独立展示量
        "clicks": None,       # 同上
    }


def _extract_rows_from_captures(
    captures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """从 NetworkRecorder 捕获的响应列表中提取目标 rows。

    只处理 DataSetKey == _TARGET_DATASET_KEY 的响应；按 material_id 去重，
    后捕获的覆盖先捕获的（因为后者可能更新）。
    """
    by_id: dict[str, dict[str, Any]] = {}
    for cap in captures:
        body = cap.get("body") or {}
        # 容错：部分响应外层是 {data, status_code}，部分是 {data, code, msg}
        data = body.get("data") or {}
        if not data:
            continue
        stats_data = data.get("statsData") or data.get("StatsData") or {}
        raw_rows = stats_data.get("rows") or stats_data.get("Rows") or []
        if not raw_rows:
            continue
        for raw in raw_rows:
            parsed = _parse_row(raw)
            if parsed is None:
                continue
            by_id[parsed["material_id"]] = parsed
    return list(by_id.values())


async def fetch_video_material_stats(
    *,
    cdp_url: str | None = None,
    collect_timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """主入口：attach CDP → navigate 入口页 → 收集 → 返回 rows。

    Args:
        cdp_url: CDP 调试端口 URL，默认读 FLOWCUT_QC_CDP_URL env。
        collect_timeout: 单页等待 NetworkRecorder.collect() 超时秒数。

    Returns:
        list of {material_id, material_name, upload_time, status_text,
                 video_id, aweme_item_id, cost, conversions, gmv, ...}。
        若所有入口 URL 都没拿到匹配响应，返回空列表。

    Raises:
        RuntimeError: CDP 连接失败 / Playwright 异常 / 所有入口导航都失败。
    """
    url = cdp_url or _default_cdp_url()
    entry_url = _build_entry_url()
    logger.info("qianchuan_scraper: attaching CDP %s", url)

    async with BrowserClient(url) as client:
        recorder = NetworkRecorder(
            client.page, pattern=_QC_MATERIAL_LIST_PATTERN,
        )
        await recorder.start()
        await recorder.clear()

        # SPA 路由：相同 URL goto 是 no-op，必须先离开当前页强制重新加载
        # 使用 Playwright 底层 page 而不是 BrowserClient.navigate 以拿更多控制
        page = client.page
        try:
            await page.goto("about:blank", wait_until="load", timeout=10000)
        except Exception as exc:
            logger.warning("qianchuan_scraper: about:blank 跳转失败 %s", exc)
        await asyncio.sleep(1.0)

        logger.info("qianchuan_scraper: navigate %s", entry_url)
        try:
            # networkidle 等所有 ajax 完成（千川 SPA 通常 5-10s）
            await page.goto(entry_url, wait_until="networkidle", timeout=45000)
        except Exception as exc:
            await recorder.stop()
            raise RuntimeError(
                f"qianchuan_scraper: navigate {entry_url} 失败: {exc}"
            ) from exc

        # 编辑推广页 → 创意 tab 自动 fire material/list-required（含目标 DataSetKey）
        captures = await recorder.collect(
            min_count=1, timeout=collect_timeout,
        )
        logger.info("qianchuan_scraper: collected %d captures", len(captures))

        # 过滤目标 DataSetKey 的响应
        target_captures = [c for c in captures if _is_target_dataset(c)]
        if not target_captures:
            # 没拿到目标 dataset，再等一阵（前端可能还在加载其它响应）
            await asyncio.sleep(5)
            captures = await recorder.dump_all()
            logger.info(
                "qianchuan_scraper: after extra wait, total captures=%d",
                len(captures),
            )
            target_captures = [c for c in captures if _is_target_dataset(c)]

        await recorder.stop()
        captures = target_captures

    if not captures:
        logger.warning(
            "qianchuan_scraper: 0 个匹配 DataSetKey=%s 的响应（页面未 fire 该接口）",
            _TARGET_DATASET_KEY,
        )
        return []

    rows = _extract_rows_from_captures(captures)
    logger.info(
        "qianchuan_scraper: captures=%d parsed_rows=%d (dataset=%s)",
        len(captures), len(rows), _TARGET_DATASET_KEY,
    )
    return rows


def _is_target_dataset(capture: dict[str, Any]) -> bool:
    """判断一条 capture 是不是目标 DataSetKey 的响应。

    无法从响应 body 直接拿到 DataSetKey（响应不回传），通过 URL 的 reqFrom 参数
    + body 含 statsData 字段做粗判即可（同一 URL 不同 reqFrom 共享接口）。
    """
    body = capture.get("body") or {}
    data = body.get("data") or {}
    stats = data.get("statsData") or data.get("StatsData") or {}
    rows = stats.get("rows") or stats.get("Rows") or []
    if not rows:
        return False
    # 抽样首行：含 materialId 才认定为视频物料维度
    first = rows[0]
    dims = first.get("dimensions") or first.get("Dimensions") or {}
    return "materialId" in dims or "material_id" in dims
