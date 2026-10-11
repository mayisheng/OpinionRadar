"""冒烟测试：配置加载、领域模型、管线描述、CLI 关键路径。"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from opinion_radar.cli import app
from opinion_radar.config import load_app_config, load_runtime_config
from opinion_radar.domain.models import ContentItem, ContentType, Platform
from opinion_radar.pipeline import Pipeline


def test_load_app_config(config_file: Path) -> None:
    app_cfg = load_app_config(config_file)
    assert app_cfg.sources[0].uid == "123456"
    assert app_cfg.llm.model == "qwen3:14b"
    assert app_cfg.notify.channel == "serverchan"


def test_load_runtime_config_reads_env(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPINION_RADAR_SERVERCHAN_SENDKEY", "test-key")
    runtime = load_runtime_config(config_file)
    assert runtime.secrets.serverchan_sendkey == "test-key"


def test_pipeline_describe(config_file: Path) -> None:
    runtime = load_runtime_config(config_file)
    steps = Pipeline(runtime).describe()
    assert [step.name for step in steps] == [
        "采集 Collectors",
        "去重 Storage",
        "提取 Extractors",
        "分析 Analysis",
        "推送 Notifiers",
    ]
    plan_text = " ".join(step.detail for step in steps)
    assert "测试博主" in plan_text
    assert "qwen3:14b" in plan_text


def test_content_item_defaults() -> None:
    item = ContentItem(
        platform=Platform.BILIBILI,
        source_id="1",
        content_type=ContentType.VIDEO,
        external_id="BV1xx",
        title="测试视频",
        url="https://www.bilibili.com/video/BV1xx",
    )
    assert item.status.value == "discovered"
    assert item.body_text is None
    assert item.is_paid is False


def test_cli_version() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "opinion-radar" in result.stdout


def test_cli_run_dry_run(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPINION_RADAR_CONFIG", str(config_file))
    result = CliRunner().invoke(app, ["run", "--dry-run"])
    assert result.exit_code == 0
    assert "测试博主" in result.stdout
    assert "qwen3:14b" in result.stdout


def test_cli_run_without_config_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPINION_RADAR_CONFIG", "missing.yaml")
    result = CliRunner().invoke(app, ["run", "--dry-run"])
    assert result.exit_code == 2
