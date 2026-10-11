"""提取层抽象。

- 视频：官方/AI 字幕优先，缺失时 yt-dlp 取音频 + mlx-whisper 转写（迭代 001）；
- 专栏：HTML 正文清洗为 Markdown（迭代 002）；
- 图文动态：文本 + 图片 OCR（迭代 002）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from opinion_radar.domain.models import ContentItem


class Extractor(ABC):
    """单一提取能力。管线按 content_type 选择对应实现。"""

    @abstractmethod
    def supports(self, item: ContentItem) -> bool:
        """是否能处理该内容。"""

    @abstractmethod
    def extract(self, item: ContentItem) -> ContentItem:
        """提取正文，填充 item.body_text 并返回同一对象。"""
