"""管线编排骨架（架构详见 docs/architecture/02-pipeline.md）。

数据流：采集 Collectors → 去重/状态 Storage → 提取 Extractors
       → AI 分析 Analysis → 汇总/推送 Notifiers
"""

from __future__ import annotations

from dataclasses import dataclass

from opinion_radar.config import RuntimeConfig


@dataclass(frozen=True)
class Step:
    """管线中的一个阶段（dry-run 自检时展示）。"""

    order: int
    name: str
    detail: str


class Pipeline:
    """端到端管线。由 CLI / launchd 每小时触发 run_once（迭代 001）。"""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def describe(self) -> list[Step]:
        """根据当前配置生成管线计划，不发起任何外部请求。"""
        app = self.config.app
        enabled_sources = [s for s in app.sources if s.enabled]
        sources = ", ".join(f"{s.name or s.uid}({s.uid})" for s in enabled_sources) or "（未配置）"
        return [
            Step(1, "采集 Collectors", f"B站增量轮询：{sources}"),
            Step(2, "去重 Storage", f"SQLite：{app.storage.database_url}"),
            Step(
                3,
                "提取 Extractors",
                f"转写策略：{app.transcription.strategy}；OCR：{app.ocr.enabled}",
            ),
            Step(4, "分析 Analysis", f"模型：{app.llm.model} @ {app.llm.base_url}"),
            Step(
                5,
                "推送 Notifiers",
                f"渠道：{app.notify.channel}；即时推送：{app.notify.immediate}",
            ),
        ]

    def run_once(self) -> None:
        """执行一次完整管线。"""
        raise NotImplementedError("端到端管线将在迭代 001 实现")
