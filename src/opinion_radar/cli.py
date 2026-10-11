"""OpinionRadar 命令行入口。

命令：
    opinion-radar doctor          环境自检（配置 / Ollama 连通性）
    opinion-radar run --dry-run   演练模式：只打印管线计划
    opinion-radar run             执行一次采集 → 提取 → 分析 → 推送（迭代 001）
    opinion-radar digest          生成并推送每日观点汇总（迭代 004）
"""

from __future__ import annotations

import httpx
import typer

from opinion_radar import __version__
from opinion_radar.config import (
    ConfigNotFoundError,
    RuntimeConfig,
    config_path,
    load_runtime_config,
)
from opinion_radar.pipeline import Pipeline
from opinion_radar.utils.logging import configure_logging

app = typer.Typer(add_completion=False, no_args_is_help=True, help="B 站财经观点雷达")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"opinion-radar {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="显示版本并退出"
    ),
) -> None:
    configure_logging()


def _load_or_exit() -> RuntimeConfig:
    """加载运行期配置；失败时打印友好信息并退出。"""
    try:
        return load_runtime_config()
    except ConfigNotFoundError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=2) from exc


@app.command()
def doctor() -> None:
    """环境自检：配置文件、监控博主、Ollama 服务。"""
    failed = False
    runtime = None
    path = config_path()

    if path.exists():
        try:
            runtime = load_runtime_config(path)
        except Exception as exc:  # 配置解析失败属于致命错误，直接展示
            typer.secho(f"[FAIL] 配置文件: {path} 解析失败：{exc}", fg=typer.colors.RED)
            raise typer.Exit(code=1) from exc
        typer.secho(f"[PASS] 配置文件: {path}", fg=typer.colors.GREEN)
        enabled = [s for s in runtime.app.sources if s.enabled]
        if enabled:
            names = ", ".join(s.name or s.uid for s in enabled)
            typer.secho(
                f"[PASS] 监控博主: 已启用 {len(enabled)} 个（{names}）",
                fg=typer.colors.GREEN,
            )
        else:
            typer.secho("[FAIL] 监控博主: 未配置启用的 source", fg=typer.colors.RED)
            failed = True
    else:
        typer.secho(
            f"[FAIL] 配置文件: {path} 不存在，请先 cp config/config.example.yaml {path}",
            fg=typer.colors.RED,
        )
        failed = True

    base_url = runtime.app.llm.base_url if runtime else "http://localhost:11434/v1"
    api_base = base_url.rstrip("/").removesuffix("/v1")
    try:
        resp = httpx.get(f"{api_base}/api/tags", timeout=2.0)
        resp.raise_for_status()
        models = [str(m.get("name", "")) for m in resp.json().get("models", [])]
        detail = "，".join(models) if models else "在线，但未安装模型（执行 make ollama-pull）"
        typer.secho(f"[PASS] Ollama: {detail}", fg=typer.colors.GREEN)
    except (httpx.HTTPError, OSError) as exc:
        typer.secho(f"[FAIL] Ollama: 不可达（{api_base}）：{exc}", fg=typer.colors.RED)
        failed = True

    if failed:
        raise typer.Exit(code=1)


@app.command()
def run(
    dry_run: bool = typer.Option(False, "--dry-run", help="只打印管线计划，不发起外部请求/写库"),
) -> None:
    """执行一次采集 → 提取 → 分析 → 推送。"""
    runtime = _load_or_exit()
    configure_logging(runtime.app.log_level)
    pipeline = Pipeline(runtime)

    if dry_run:
        typer.secho("dry-run：仅展示管线计划，不发起任何外部请求/写库", fg=typer.colors.YELLOW)
        for step in pipeline.describe():
            typer.echo(f"[{step.order}] {step.name} — {step.detail}")
        return

    try:
        pipeline.run_once()
    except NotImplementedError as exc:
        typer.secho(f"尚未实现：{exc}", fg=typer.colors.RED)
        raise typer.Exit(code=3) from exc


@app.command()
def digest(
    dry_run: bool = typer.Option(False, "--dry-run", help="只显示 digest 配置"),
) -> None:
    """生成并推送每日观点汇总（迭代 004）。"""
    runtime = _load_or_exit()
    configure_logging(runtime.app.log_level)
    daily = runtime.app.notify.daily_digest
    if dry_run:
        typer.secho(
            f"dry-run：每日 digest 已启用={daily.enabled}，推送时间={daily.time}",
            fg=typer.colors.YELLOW,
        )
        return
    # 迭代 004 前优雅跳过（launchd 每日触发时不产生错误日志）
    typer.secho("每日 digest 将在迭代 004 实现，本次跳过", fg=typer.colors.YELLOW)
