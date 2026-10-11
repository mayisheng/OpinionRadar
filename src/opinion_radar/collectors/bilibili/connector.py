"""B 站采集器：Playwright 真实浏览器响应拦截实现（ADR-0005）。

当前阶段（迭代 001 第一段）：空间动态流增量发现（图文 / 视频 / 转发）。
后续阶段：播放页字幕拦截（fetch_detail）、评论区博主回复抓取。
"""

from __future__ import annotations

from datetime import datetime

from loguru import logger

from opinion_radar.collectors.base import Connector
from opinion_radar.collectors.bilibili.browser import BilibiliBrowser
from opinion_radar.domain.models import ContentItem, Platform


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
        # TODO(迭代 001): 播放页拦截 player/wbi/v2 获取字幕地址，无字幕走 whisper 回退
        # TODO(后续阶段): 评论主楼 + 楼中楼拦截，按 member.mid 过滤博主回复拼入 body_text
        raise NotImplementedError("B 站详情/字幕/评论拦截将在迭代 001 后续阶段实现")
