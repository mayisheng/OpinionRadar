"""B 站采集器：Playwright 真实浏览器响应拦截实现（ADR-0005）。

当前阶段（迭代 001 第三段）：动态流增量发现 + 图文动态详情 + 视频字幕拦截。
后续阶段：评论区博主回复抓取。
"""

from __future__ import annotations

from datetime import datetime

import httpx
from loguru import logger

from opinion_radar.collectors.base import Connector
from opinion_radar.collectors.bilibili.browser import BilibiliBrowser
from opinion_radar.collectors.bilibili.subtitle import (
    SUBTITLE_ERROR,
    SUBTITLE_MISSING,
    SUBTITLE_OK,
    SUBTITLE_PAID_UNREACHABLE,
    download_subtitle_transcript,
)
from opinion_radar.collectors.exceptions import CollectorError
from opinion_radar.domain.models import ContentItem, ContentType, Platform, ProcessingStatus


class BilibiliConnector(Connector):
    platform = Platform.BILIBILI

    def list_updates(self, since: datetime) -> list[ContentItem]:
        """打开博主空间动态页，拦截动态流并返回 since 之后的新内容。"""
        logger.info(
            f"list_updates_start source={self.source.name or self.source.uid} "
            f"uid={self.source.uid} since={since.isoformat()}"
        )
        with BilibiliBrowser(self.browser_config) as browser:
            items = browser.list_dynamics(uid=self.source.uid, since=since)
        logger.info(f"list_updates_done uid={self.source.uid} items={len(items)}")
        return items

    def fetch_detail(self, item: ContentItem) -> ContentItem:
        """获取单条内容详情：图文动态走详情页拦截，视频走播放页字幕拦截。"""
        if item.content_type is ContentType.VIDEO:
            merged = self.fetch_video_details([item])
            detail = merged.get(item.external_id)
            if detail is None:
                raise CollectorError(f"视频字幕抓取失败：bvid={item.external_id}")
            return detail
        dynamic_id = str(item.payload.get("dynamic_id") or item.external_id)
        merged = self.fetch_details([item])
        detail = merged.get(dynamic_id)
        if detail is None:
            raise CollectorError(f"动态详情抓取失败：dynamic_id={dynamic_id}")
        return detail

    def fetch_details(self, items: list[ContentItem]) -> dict[str, ContentItem]:
        """批量抓取图文动态详情，返回 dynamic_id → 详情 ContentItem。

        整批共用一个浏览器会话（条间人类节奏停顿），详情字段由调用方合并回原条目。
        """
        dynamic_ids = [str(i.payload.get("dynamic_id") or i.external_id) for i in items]
        logger.info(f"fetch_details_start uid={self.source.uid} count={len(dynamic_ids)}")
        with BilibiliBrowser(self.browser_config) as browser:
            results = browser.fetch_dynamic_details(dynamic_ids, uid=self.source.uid)
        logger.info(
            f"fetch_details_done uid={self.source.uid} ok={len(results)}/{len(dynamic_ids)}"
        )
        return results

    def fetch_video_details(self, items: list[ContentItem]) -> dict[str, ContentItem]:
        """批量抓取视频字幕，返回 bvid → 填充了正文 / 付费标记的条目副本。

        播放页拦截 player 接口拿到字幕轨道后，字幕 CDN 直连下载（不经浏览器）。
        合规处理：未付费的充电专属视频标记「付费不可达」，不请求字幕；
        已付费的仅提取字幕文字。调用方把字段合并回原条目（merge_video_detail）。
        """
        bvids = [item.external_id for item in items]
        logger.info(f"fetch_video_details_start uid={self.source.uid} count={len(bvids)}")
        with BilibiliBrowser(self.browser_config) as browser:
            infos = browser.fetch_video_players(bvids)

        results: dict[str, ContentItem] = {}
        for item in items:
            bvid = item.external_id
            info = infos.get(bvid)
            if info is None:
                logger.warning(f"video_player_info_missing bvid={bvid}, skip")
                continue
            detail = item.model_copy(deep=True)
            if info.is_paid and not info.paid_playable:
                # 合规红线：未付费的充电专属内容标记「付费不可达」，不请求字幕
                detail.is_paid = True
                detail.body_text = None
                detail.payload["subtitle_status"] = SUBTITLE_PAID_UNREACHABLE
            elif info.subtitle_url:
                try:
                    transcript = download_subtitle_transcript(info.subtitle_url)
                except (httpx.HTTPError, ValueError) as exc:
                    logger.warning(f"subtitle_download_failed bvid={bvid} error={exc}")
                    detail.payload["subtitle_status"] = SUBTITLE_ERROR
                    results[bvid] = detail
                    continue
                detail.is_paid = info.is_paid
                detail.body_text = transcript or None
                detail.payload["subtitle_status"] = SUBTITLE_OK if transcript else SUBTITLE_MISSING
                detail.payload["subtitle_lan"] = info.subtitle_lan
                if transcript and detail.status is ProcessingStatus.DISCOVERED:
                    detail.status = ProcessingStatus.FETCHED
            else:
                detail.payload["subtitle_status"] = SUBTITLE_MISSING
            results[bvid] = detail

        logger.info(
            f"fetch_video_details_done uid={self.source.uid} ok={len(results)}/{len(bvids)}"
        )
        return results
