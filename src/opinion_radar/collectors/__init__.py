"""采集层：各平台 Connector 实现。"""

from opinion_radar.collectors.base import Connector
from opinion_radar.collectors.bilibili import BilibiliConnector
from opinion_radar.config import RuntimeConfig

__all__ = ["BilibiliConnector", "Connector", "build_connectors"]


def build_connectors(runtime: RuntimeConfig) -> list[Connector]:
    """按配置实例化所有启用博主的 Connector（目前仅 B 站）。"""
    app = runtime.app
    return [
        BilibiliConnector(
            source=source,
            collector_config=app.collector,
            secrets=runtime.secrets,
            browser_config=app.browser,
        )
        for source in app.sources
        if source.enabled
    ]
