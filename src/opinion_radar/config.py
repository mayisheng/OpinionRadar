"""配置加载。

两类配置严格分离：
- 非敏感配置：config/config.yaml（博主、调度、模型、推送开关），可入库；
- 敏感配置：.env / 环境变量（SESSDATA、推送 Key、API Key），永不入库。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CONFIG_PATH = Path("config/config.yaml")
ENV_CONFIG_PATH = "OPINION_RADAR_CONFIG"


class ConfigNotFoundError(FileNotFoundError):
    """配置文件不存在时抛出，便于 CLI 给出友好提示。"""


class SourceConfig(BaseModel):
    uid: str
    name: str | None = None
    enabled: bool = True


class CollectorConfig(BaseModel):
    min_interval_seconds: float = 6.0
    interval_jitter_seconds: float = 30.0
    request_timeout_seconds: float = 15.0
    page_size: int = 20


class LLMConfig(BaseModel):
    base_url: str = "http://localhost:11434/v1"
    model: str = "qwen3:14b"
    timeout_seconds: float = 180.0
    max_retries: int = 2
    fallback_enabled: bool = False  # 云端兜底，迭代 005


class TranscriptionConfig(BaseModel):
    strategy: str = "subtitle_first"  # subtitle_first | whisper_only
    whisper_model: str = "mlx-community/whisper-large-v3-turbo"
    max_concurrency: int = 1


class OcrConfig(BaseModel):
    enabled: bool = True
    engine: str = "rapidocr"


class DailyDigestConfig(BaseModel):
    enabled: bool = True
    time: str = "20:00"


class NotifyConfig(BaseModel):
    channel: str = "serverchan"  # serverchan | pushplus | bark | telegram | feishu
    immediate: bool = True
    daily_digest: DailyDigestConfig = Field(default_factory=DailyDigestConfig)


class StorageConfig(BaseModel):
    database_url: str = "sqlite:///./data/opinion_radar.db"
    raw_dir: Path = Path("./data/raw")


class AppConfig(BaseModel):
    """config.yaml 的完整结构。"""

    sources: list[SourceConfig] = Field(default_factory=list)
    collector: CollectorConfig = Field(default_factory=CollectorConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    transcription: TranscriptionConfig = Field(default_factory=TranscriptionConfig)
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    log_level: str = "INFO"


class Secrets(BaseSettings):
    """敏感信息：只从环境变量 / .env 读取，前缀 OPINION_RADAR_。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="OPINION_RADAR_",
        extra="ignore",
    )

    bilibili_sessdata: str | None = None
    serverchan_sendkey: str | None = None
    pushplus_token: str | None = None
    llm_api_key: str | None = None


class RuntimeConfig(BaseModel):
    """运行期配置包：业务配置 + 密钥，全管线共享同一对象。"""

    app: AppConfig
    secrets: Secrets


def config_path() -> Path:
    """当前配置文件路径（允许用环境变量覆盖）。"""
    return Path(os.environ.get(ENV_CONFIG_PATH, DEFAULT_CONFIG_PATH))


def load_app_config(path: str | Path | None = None) -> AppConfig:
    cfg_path = Path(path) if path is not None else config_path()
    if not cfg_path.exists():
        raise ConfigNotFoundError(
            f"配置文件不存在：{cfg_path}，请先执行 cp config/config.example.yaml {cfg_path}"
        )
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data)


def load_runtime_config(path: str | Path | None = None) -> RuntimeConfig:
    return RuntimeConfig(app=load_app_config(path), secrets=Secrets())
