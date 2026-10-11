"""loguru 日志配置：彩色控制台 + 按大小轮转的文件日志。

使用方式：进程启动时调用一次 configure_logging()，之后直接
`from loguru import logger` 使用。
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def configure_logging(level: str = "INFO", log_dir: Path = Path("data/logs")) -> None:
    """初始化全局日志。重复调用会重置 sink（测试友好）。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:MM-DD HH:mm:ss}</green> "
            "<level>{level: <7}</level> "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
        ),
    )
    logger.add(
        log_dir / "opinion_radar.log",
        level=level,
        rotation="10 MB",
        retention="14 days",
        encoding="utf-8",
        enqueue=True,  # 多线程/退出时安全
    )
