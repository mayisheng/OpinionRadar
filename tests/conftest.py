"""pytest 公共夹具。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """每个测试在独立临时目录运行，避免日志/数据库污染仓库。"""
    monkeypatch.chdir(tmp_path)
    yield


@pytest.fixture()
def config_file(tmp_path: Path) -> Path:
    """写入一份最小可用配置并返回路径。"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
sources:
  - uid: "123456"
    name: "测试博主"
llm:
  model: "qwen3:14b"
notify:
  channel: "serverchan"
""".lstrip(),
        encoding="utf-8",
    )
    return cfg
