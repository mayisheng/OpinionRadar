"""B 站采集器骨架。

TODO(迭代 001): wbi 签名、UP 主视频列表增量轮询、官方/AI 字幕拉取。
TODO(迭代 002): 专栏、图文动态、充电专属（SESSDATA 鉴权）。
技术选型理由（httpx 直连 vs 第三方 SDK）见 ADR-0003。
"""

from __future__ import annotations

from datetime import datetime

from opinion_radar.collectors.base import Connector
from opinion_radar.domain.models import ContentItem, Platform


class BilibiliConnector(Connector):
    platform = Platform.BILIBILI

    def list_updates(self, since: datetime) -> list[ContentItem]:
        raise NotImplementedError("B 站列表增量轮询将在迭代 001 实现")

    def fetch_detail(self, item: ContentItem) -> ContentItem:
        raise NotImplementedError("B 站详情/字幕拉取将在迭代 001、迭代 002 实现")
