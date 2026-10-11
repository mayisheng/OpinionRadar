"""B 站采集器：Playwright 真实浏览器响应拦截实现（ADR-0005）。

当前阶段（迭代 001 第二段）：动态流增量发现 + 动态详情（正文/图片）拦截。
后续阶段：播放页字幕拦截、评论区博主回复抓取。
"""

from __future__ import annotations

from datetime import datetime

from loguru import logger

from opinion_radar.collectors.base import Connector
from opinion_radar.collectors.bilibili.browser import BilibiliBrowser
from opinion_radar.collectors.exceptions import CollectorError
from opinion_radar.domain.models import ContentItem, ContentType, Platform


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
        """获取单条动态详情；视频字幕 / 评论拦截在迭代 001 后续阶段实现。"""
        if item.content_type is not ContentType.MOMENT:
            raise NotImplementedError(
                "B 站视频字幕/评论拦截将在迭代 001 后续阶段实现，当前仅支持图文动态"
            )
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
