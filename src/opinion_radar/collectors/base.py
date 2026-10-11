"""采集层抽象：可插拔 Connector 接口（决策见 ADR-0003）。

新增平台（微信公众号 / 雪球 / 微博 …）时实现本接口即可接入管线，
上层提取、分析、推送无需任何改动。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from opinion_radar.config import BrowserConfig, CollectorConfig, Secrets, SourceConfig
from opinion_radar.domain.models import ContentItem, Platform


class Connector(ABC):
    """单个平台的采集器。一个博主对应一个 Connector 实例。"""

    platform: Platform

    def __init__(
        self,
        source: SourceConfig,
        collector_config: CollectorConfig,
        secrets: Secrets,
        browser_config: BrowserConfig | None = None,
    ) -> None:
        self.source = source
        self.collector_config = collector_config
        self.secrets = secrets
        # 浏览器采集配置（ADR-0005）；非浏览器平台不用，给默认值即可
        self.browser_config = browser_config or BrowserConfig()

    @abstractmethod
    def list_updates(self, since: datetime) -> list[ContentItem]:
        """轮询博主自 since 以来的新内容（视频 / 专栏 / 动态）。

        实现要求：限速 + 抖动、分页、不抛瞬时错误（重试/降级）。
        """

    @abstractmethod
    def fetch_detail(self, item: ContentItem) -> ContentItem:
        """获取单条内容详情。付费内容在此处理鉴权，正文留给 Extractor。"""
