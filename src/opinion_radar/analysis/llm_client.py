"""LLM 客户端：OpenAI 兼容协议，默认指向本机 Ollama（决策见 ADR-0002）。

切换云端模型（DeepSeek 等）只需修改 config.llm.base_url / model 并配置
LLM_API_KEY，业务代码无感知。本地模型不可用时的云端兜底在迭代 005 实现。
"""

from __future__ import annotations

from opinion_radar.analysis.schemas import AnalysisResult
from opinion_radar.config import LLMConfig
from opinion_radar.domain.models import ContentItem


class LLMClient:
    """OpenAI 兼容聊天补全客户端。"""

    def __init__(self, config: LLMConfig, api_key: str | None = None) -> None:
        self.config = config
        self._api_key = api_key
        # TODO(迭代 001): self._client = openai.OpenAI(
        #     base_url=config.base_url, api_key=api_key or "ollama", timeout=...
        # )

    def analyze(self, item: ContentItem, *, prompt_version: str = "v0") -> AnalysisResult:
        """分类 + 摘要 + 观点抽取，返回经 schema 校验的 AnalysisResult。"""
        raise NotImplementedError("LLM 分析将在迭代 001 实现，观点结构化在迭代 003 增强")
