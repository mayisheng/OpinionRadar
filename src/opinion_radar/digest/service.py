"""摘要聚合服务骨架。

迭代 004 实现：把一天内多条 Summary 聚合为日报（观点去重合并、
按标的/方向分组、Markdown 模板渲染、超长切分）。
"""

from __future__ import annotations

from opinion_radar.config import RuntimeConfig


class DigestService:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def render_daily(self) -> str:
        """生成当日观点汇总（Markdown）。"""
        raise NotImplementedError("每日 digest 将在迭代 004 实现")
