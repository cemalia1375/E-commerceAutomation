"""千川投放 publisher：通过 BrowserClient 上传成片 + 创建全域推广计划。

录制于 2026-05-29，通过 NetworkRecorder 抓真实接口反推：

    1. open dialog -> POST /ad/api/creation/material/auth-key            (STS 凭证)
    2. set_files() -> POST vod.bytedanceapi ApplyUploadInner             (拿 Vid)
       (binary 上传到 tos-d-x-hl.snssdk.com)
    3. upload done -> POST vod.bytedanceapi CommitUploadInner            (确认 Vid)
    4.            -> POST /ad/api/creation/v1/material/set-video-status-to-public
    5.            -> POST /ad/api/creation/v1/material/get-video-play-info
    6. 点"确定"   -> POST /ad/api/creation/material/bind-video-to-owner
                     ► 响应 data.vidToMidMap[Vid] = material_id (19位数字)
    7. 点外层"保存"-> POST /ad/api/pmc/v1/uni-promotion/material/add-uni-prom-materials
                     ► status_code=0 表示挂载到 ad_id 成功

文件命名约定：上传前 rename 成 ``fc-{creative_id}-{safe_title}.mp4``，
便于 qianchuan_scraper 用正则 ``fc-(\\d+)-`` 反向认领。

dry-run 模式：FLOWCUT_QC_PUBLISH_DRY_RUN=1（默认），跳过浏览器，返回伪造 ID。

依赖 env：
  * FLOWCUT_QC_ADVERTISER_ID  -- 千川账号 aavid（默认 1852831850913418）
  * FLOWCUT_QC_AD_ID          -- 已有的全域推广计划 adId（默认 1852918923016212）
  * FLOWCUT_QC_PUBLISH_DRY_RUN  -- "1" 走 dry-run，"0" 走真实浏览器流程
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from typing import Optional

from Flowcut.browser.client import BrowserClient
from Flowcut.browser.network import NetworkRecorder

logger = logging.getLogger(__name__)


# 字节 VOD 上传服务 CommitUploadInner 响应里 Result.Results[0].Vid 即上传产物 Vid
# （比 get-video-play-info 更稳：dedup 命中时千川可能跳过 play-info，但 commit 一定触发）
_VOD_HOST = "vod.bytedanceapi"
# 点外层"保存"后，/v1/ad/update 是聚合保存接口，status_code=0 即成功
_AD_UPDATE_PATTERN = "/v1/ad/update"


@dataclass(frozen=True)
class PublishResult:
    """publisher 返回值，executor 据此回写 fc_creative。"""

    material_id: str
    campaign_id: str
    dry_run: bool = False
    snapshot: Optional[str] = None  # 失败/调试时 dump 的 aria_snapshot


class PublishError(Exception):
    """publisher 失败时抛出；executor 捕获后转 TaskExecutionResult.failed。

    ``snapshot`` 字段供上层 AI fallback 用。
    """

    def __init__(self, message: str, *, snapshot: Optional[str] = None) -> None:
        super().__init__(message)
        self.snapshot = snapshot


def _is_dry_run() -> bool:
    return os.getenv("FLOWCUT_QC_PUBLISH_DRY_RUN", "1") == "1"


def _get_qc_ids() -> tuple[str, str]:
    """从 env 读 (advertiser_id, ad_id)，跟 qianchuan_scraper.py 复用同一对。"""
    advertiser_id = os.getenv("FLOWCUT_QC_ADVERTISER_ID", "1852831850913418")
    ad_id = os.getenv("FLOWCUT_QC_AD_ID", "1852918923016212")
    return advertiser_id, ad_id


def _build_entry_url(advertiser_id: str, ad_id: str) -> str:
    """全域推广计划编辑页 → 创意 tab。"""
    return (
        "https://qianchuan.jinritemai.com/uni-creation/overall-live"
        f"?aavid={advertiser_id}&adId={ad_id}&anchor=creative&type=edit"
    )


_FILENAME_SAFE_RE = re.compile(r"[^\w一-鿿]+")


def _make_upload_filename(creative_id: int, title: str) -> str:
    """把 title 压成合法文件名前缀，对齐 scraper 的 fc-(\\d+)- 反向认领正则。"""
    safe = _FILENAME_SAFE_RE.sub("", title)[:30] or "creative"
    return f"fc-{creative_id}-{safe}.mp4"


async def publish_creative_via_browser(
    *,
    local_video_path: str,
    title: str,
    cdp_url: str,
    creative_id: int,
) -> PublishResult:
    """主入口：attach CDP → 上传成片 → 创建计划 → 返回 material/campaign ID。

    Args:
        local_video_path: 已经从 OSS 下载到本地的成片文件路径。
        title: 千川广告标题（当前仅用作文件名前缀，不写入千川标题池）。
        cdp_url: 长驻 Chrome 的 CDP endpoint（由 start-chrome-qianchuan.sh 拉起）。
        creative_id: 关联的 fc_creative.id，用于命名 + 日志。

    Returns:
        PublishResult 含 material_id / campaign_id (= ad_id)。

    Raises:
        PublishError: 任何一步失败；snapshot 字段尽量带上当时的 aria_snapshot。
    """
    if _is_dry_run():
        fake_mid = f"dry-mid-{uuid.uuid4().hex[:12]}"
        fake_cid = f"dry-cid-{uuid.uuid4().hex[:12]}"
        logger.warning(
            "qianchuan_publisher: DRY_RUN mode — 返回伪造 ID "
            "(creative=%d, material=%s, campaign=%s)",
            creative_id, fake_mid, fake_cid,
        )
        return PublishResult(
            material_id=fake_mid, campaign_id=fake_cid, dry_run=True,
        )

    return await _publish_real(
        local_video_path=local_video_path,
        title=title,
        cdp_url=cdp_url,
        creative_id=creative_id,
    )


async def _dump_snapshot(page) -> Optional[str]:
    """尽力 dump 一份 body aria_snapshot 用于 AI fallback / debug。"""
    try:
        snap = await page.locator("body").aria_snapshot()
        return snap[-4000:]  # 截尾，因为关键内容（dialog/toast）通常在末尾
    except Exception as exc:  # noqa: BLE001
        logger.warning("qianchuan_publisher: aria_snapshot 失败: %s", exc)
        return None


async def _publish_real(
    *,
    local_video_path: str,
    title: str,
    cdp_url: str,
    creative_id: int,
) -> PublishResult:
    """真实点击流程：录制于 2026-05-29，全域推广计划"加创意到已有计划"。"""
    advertiser_id, ad_id = _get_qc_ids()
    entry_url = _build_entry_url(advertiser_id, ad_id)
    upload_name = _make_upload_filename(creative_id, title)

    # 把源文件复制+重命名到临时目录，让上传到千川的文件名带 fc-<id>- 前缀
    tmpdir = tempfile.mkdtemp(prefix=f"flowcut-publish-rename-{creative_id}-")
    renamed_path = os.path.join(tmpdir, upload_name)
    shutil.copy(local_video_path, renamed_path)
    logger.info(
        "qianchuan_publisher: creative=%d upload as %s -> %s",
        creative_id, upload_name, entry_url,
    )

    snapshot: Optional[str] = None
    try:
        async with BrowserClient(cdp_url) as client:
            page = client.page

            # 上传整段时间内监听 vod.bytedanceapi 的 CommitUploadInner 响应，抽 Vid
            # （比依赖 dialog 确定触发的 get-video-play-info 更稳：后者在 dedup
            # 命中时千川可能跳过；CommitUploadInner 是上传必经路径）
            vid_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

            async def _try_extract_vid(resp) -> None:
                if vid_future.done():
                    return
                try:
                    body = await resp.json()
                except Exception:  # noqa: BLE001
                    return
                result = body.get("Result") if isinstance(body, dict) else None
                if not isinstance(result, dict):
                    return
                results = result.get("Results")
                if not isinstance(results, list) or not results:
                    return
                vid_val = results[0].get("Vid")
                if vid_val and not vid_future.done():
                    vid_future.set_result(str(vid_val))

            def _on_vod_resp(r) -> None:
                if _VOD_HOST in r.url:
                    asyncio.create_task(_try_extract_vid(r))

            page.on("response", _on_vod_resp)

            try:
                # 1) 跳到全域推广计划编辑页 - 创意 tab
                await page.goto(entry_url, wait_until="networkidle", timeout=45000)
                await asyncio.sleep(1.0)

                # 2) 点 "添加视频" → 弹 dialog
                await page.get_by_role("button", name="添加视频").first.click()
                await asyncio.sleep(0.5)

                # 3) 切到 "上传视频" tab
                await page.get_by_text("上传视频", exact=True).first.click()
                await asyncio.sleep(0.5)

                # 4) 点 "点击上传" → 拦截 file chooser → 喂文件
                async with page.expect_file_chooser() as fc_info:
                    await page.get_by_text("点击上传", exact=False).first.click()
                chooser = await fc_info.value
                await chooser.set_files(renamed_path)

                # 5) 等上传完成（文件名出现在 dialog 表格里）+ buffer 给 CommitUpload/set-public 跑
                await page.get_by_text(upload_name).first.wait_for(timeout=180_000)
                await asyncio.sleep(2.5)

                # 6) 上传期间监听器应该已经拿到 Vid（CommitUploadInner 响应）
                try:
                    vid = await asyncio.wait_for(vid_future, timeout=30)
                except asyncio.TimeoutError:
                    raise PublishError(
                        "qianchuan_publisher: 30s 内没从 vod.bytedanceapi.CommitUploadInner 响应"
                        "里抽到 Vid（上传是否成功？）",
                    )
                logger.info(
                    "qianchuan_publisher: 拿到 Vid=%s (creative=%d)",
                    vid, creative_id,
                )

                # 7) 点 dialog "确定" → dialog 关闭，素材进外层编辑页草稿
                await page.get_by_role("button", name="确定", exact=True).last.click()
                await asyncio.sleep(2.0)

                # 8) 提前挂 /v1/ad/update 监听 → 再点外层 "保存"
                try:
                    async with page.expect_response(
                        lambda r: _AD_UPDATE_PATTERN in r.url,
                        timeout=60_000,
                    ) as update_info:
                        await page.get_by_role(
                            "button", name="保存", exact=True,
                        ).last.click()
                    update_resp = await update_info.value
                except Exception as exc:
                    raise PublishError(
                        f"qianchuan_publisher: 点保存后未拿到 /v1/ad/update: {exc}",
                    ) from exc
                update_body = await update_resp.json()
                if update_body.get("status_code") != 0:
                    raise PublishError(
                        f"qianchuan_publisher: ad/update 失败 status_code="
                        f"{update_body.get('status_code')} msg={update_body.get('message')}: {update_body}",
                    )

                # 8) Vid 作占位 material_id；scraper 按文件名 fc-(\\d+)- 反向认领时
                #    会覆盖成 19 位真 material_id（首次 sync 后该字段就准了）
                material_id = vid

                logger.info(
                    "qianchuan_publisher: 全流程成功 creative=%d material_id=%s ad_id=%s",
                    creative_id, material_id, ad_id,
                )
                return PublishResult(
                    material_id=material_id,
                    campaign_id=str(ad_id),
                    dry_run=False,
                )

            except Exception:
                snapshot = await _dump_snapshot(page)
                raise

    except PublishError as exc:
        # 已经是 PublishError，补 snapshot 后透传
        if exc.snapshot is None and snapshot:
            raise PublishError(str(exc), snapshot=snapshot) from exc
        raise
    except Exception as exc:
        raise PublishError(
            f"qianchuan_publisher: 未预期错误 creative={creative_id}: {exc}",
            snapshot=snapshot,
        ) from exc
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
