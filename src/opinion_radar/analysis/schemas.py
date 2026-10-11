"""LLM 结构化输出 schema。

要求模型以 JSON 返回，由 pydantic 校验；校验失败按重试策略处理。
迭代 001 先使用 tldr + category，迭代 003 强化观点结构化与去重。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from opinion_radar.domain.models import Viewpoint


class AnalysisResult(BaseModel):
    """模型对单条内容的分析结果。"""

    category: str = Field(
        description="内容分类：行情点评 / 行业分析 / 个股观点 / 宏观 / 公告解读 / 其他"
    )
    tldr: str = Field(description="一句话摘要，不超过 50 字")
    viewpoints: list[Viewpoint] = Field(default_factory=list)
    sentiment: str | None = Field(default=None, description="整体情绪：乐观 / 谨慎 / 中性")
    key_tags: list[str] = Field(default_factory=list)
