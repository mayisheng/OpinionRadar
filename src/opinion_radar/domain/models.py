"""领域模型：采集、分析、推送各层共享的数据结构。

设计原则：
- 跨平台统一（ContentItem 不绑定 B 站字段，平台特有数据放 payload）；
- 所有模型为 pydantic 模型，天然支持校验与序列化；
- 与数据库 ORM 模型分离（ORM 在迭代 001 引入，负责持久化映射）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class Platform(StrEnum):
    """内容平台。首期仅 B 站，后续随 Connector 扩展。"""

    BILIBILI = "bilibili"


class ContentType(StrEnum):
    """内容类型。"""

    VIDEO = "video"  # 视频（含充电专属视频）
    COLUMN = "column"  # 专栏图文
    MOMENT = "moment"  # 图文动态 / 短观点
    PAID_ARTICLE = "paid_article"  # 付费图文（迭代 002）


class ProcessingStatus(StrEnum):
    """单条内容在管线中的处理状态（状态机见 docs/architecture/03-data-model.md）。"""

    DISCOVERED = "discovered"  # 已发现，未提取正文
    FETCHED = "fetched"  # 正文/字幕已就绪
    ANALYZED = "analyzed"  # LLM 分析完成
    PUSHED = "pushed"  # 已推送
    FAILED = "failed"  # 任一阶段失败（带错误信息，等待重试）


class Direction(StrEnum):
    """观点方向。"""

    BULLISH = "bullish"  # 看多
    BEARISH = "bearish"  # 看空
    NEUTRAL = "neutral"  # 中性 / 观望


class ContentItem(BaseModel):
    """采集层产出的统一内容单元（跨平台通用）。"""

    platform: Platform
    source_id: str = Field(description="博主 UID")
    content_type: ContentType
    external_id: str = Field(description="平台侧内容唯一 ID，如 BV 号 / 专栏 cvid")
    title: str
    url: HttpUrl
    published_at: datetime | None = None
    is_paid: bool = False
    payload: dict[str, Any] = Field(default_factory=dict, description="平台原始响应留档")

    # 提取层填充
    body_text: str | None = Field(default=None, description="正文 / 字幕 / OCR 合并文本")
    status: ProcessingStatus = ProcessingStatus.DISCOVERED


class Viewpoint(BaseModel):
    """从内容中抽取出的单个投资观点。"""

    subject: str = Field(description="标的，如指数 / 行业 / 个股 / 宏观变量")
    direction: Direction = Direction.NEUTRAL
    thesis: str = Field(description="核心逻辑 / 结论")
    evidence: str | None = Field(default=None, description="论据 / 数据支撑")
    risk: str | None = Field(default=None, description="风险提示")
    horizon: str | None = Field(default=None, description="时间维度 / 观点时间点")


class Summary(BaseModel):
    """单条内容的 AI 分析结果（持久化与推送的依据）。"""

    external_id: str
    tldr: str = Field(description="一句话摘要")
    viewpoints: list[Viewpoint] = Field(default_factory=list)
    sentiment: str | None = Field(default=None, description="整体情绪：乐观 / 谨慎 / 中性")
    tags: list[str] = Field(default_factory=list)
    model: str = Field(description="生成该结果的模型标识")
    prompt_version: str = Field(description="Prompt 模板版本，便于回溯质量")
    created_at: datetime
