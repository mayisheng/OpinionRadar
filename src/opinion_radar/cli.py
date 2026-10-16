"""OpinionRadar 命令行入口。

命令：
    opinion-radar doctor            环境自检（配置 / Ollama 连通性）
    opinion-radar bilibili-login    有头浏览器扫码登录，持久化登录态（ADR-0005）
    opinion-radar discover          探测：真实拉取空间动态流并打印，不写库不推送
    opinion-radar archive           回溯归档：抓取指定年份以来全部动态，写入本地 JSON
    opinion-radar backfill-detail   回填归档中缺正文/图片的图文动态详情
    opinion-radar backfill-subtitle 回填归档中缺字幕正文的视频（播放页拦截）
    opinion-radar backfill-comments 回填归档中缺评论区 UP 主回复的条目（拼入正文）
    opinion-radar run --dry-run     演练模式：只打印管线计划
    opinion-radar run               执行一次采集 → 提取 → 分析 → 推送（迭代 001）
    opinion-radar digest            生成并推送每日观点汇总（迭代 004）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import typer

from opinion_radar import __version__
from opinion_radar.collectors import build_connectors
from opinion_radar.collectors.base import Connector
from opinion_radar.collectors.bilibili.browser import BilibiliBrowser
from opinion_radar.collectors.exceptions import (
    BrowserRuntimeMissingError,
    CollectorError,
    LoginRequiredError,
    RiskControlBlocked,
)
from opinion_radar.config import (
    ConfigNotFoundError,
    RuntimeConfig,
    config_path,
    load_runtime_config,
)
from opinion_radar.pipeline import Pipeline
from opinion_radar.storage.archive import (
    archive_items,
    download_item_images,
    load_archive_items,
    merge_comments,
    merge_detail,
    merge_video_detail,
    needs_comments,
    needs_detail,
    needs_video_detail,
    overwrite_archive_item,
)
from opinion_radar.utils.logging import configure_logging

app = typer.Typer(add_completion=False, no_args_is_help=True, help="B 站财经观点雷达")

# 视频字幕回填的批大小：每批一个新浏览器会话，规避长会话 CDP 挂起（见 _backfill_connector_videos）
_VIDEO_BATCH_SIZE = 5
# 评论回填的批大小：同字幕分批逻辑，长会话分批 + 逐批落盘
_COMMENT_BATCH_SIZE = 5


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


@app.command("bilibili-login")
def bilibili_login() -> None:
    """有头浏览器扫码登录 B 站，登录态持久化到 browser.profile_dir。"""
    runtime = _load_or_exit()
    configure_logging(runtime.app.log_level)
    typer.secho("即将打开有头浏览器，请用专用小号扫码登录（切勿使用主号）…", fg=typer.colors.YELLOW)
    try:
        with BilibiliBrowser(runtime.app.browser, headless=False) as browser:
            logged_in = browser.wait_for_login()
    except BrowserRuntimeMissingError as exc:
        typer.secho(f"[FAIL] {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=4) from exc
    except CollectorError as exc:
        typer.secho(f"[FAIL] 登录过程异常：{exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    if logged_in:
        typer.secho(
            f"[PASS] 登录成功，登录态已保存到 {runtime.app.browser.profile_dir}（请勿提交/外传）",
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho("[FAIL] 登录超时，未检测到登录态，请重试", fg=typer.colors.RED)
        raise typer.Exit(code=1)


@app.command()
def discover(
    hours: float | None = typer.Option(
        None, "--hours", "-h", help="向回看的小时数，默认取 collector.lookback_hours"
    ),
    headed: bool = typer.Option(False, "--headed", help="有头模式运行，便于观察页面与人工介入"),
) -> None:
    """探测命令：真实拉取各博主空间动态流并打印，不写库、不分析、不推送。"""
    runtime = _load_or_exit()
    configure_logging(runtime.app.log_level)
    if headed:
        runtime.app.browser.headless = False
    lookback = hours if hours is not None else runtime.app.collector.lookback_hours
    since = datetime.now(UTC) - timedelta(hours=lookback)
    typer.secho(
        f"discover：拉取近 {lookback:g} 小时动态（since={since.isoformat()}）",
        fg=typer.colors.CYAN,
    )

    failed = False
    for connector in build_connectors(runtime):
        name = connector.source.name or connector.source.uid
        typer.echo(f"\n== {name}（uid={connector.source.uid}）==")
        try:
            items = connector.list_updates(since)
        except LoginRequiredError as exc:
            typer.secho(f"[FAIL] {exc}，请先执行 opinion-radar bilibili-login", fg=typer.colors.RED)
            failed = True
            continue
        except RiskControlBlocked as exc:
            typer.secho(f"[FAIL] 命中风控已熔断：{exc}", fg=typer.colors.RED)
            failed = True
            continue
        except BrowserRuntimeMissingError as exc:
            typer.secho(f"[FAIL] {exc}", fg=typer.colors.RED)
            failed = True
            continue
        except CollectorError as exc:
            typer.secho(f"[FAIL] 采集失败：{exc}", fg=typer.colors.RED)
            failed = True
            continue

        if not items:
            typer.secho("时间窗内没有新动态", fg=typer.colors.YELLOW)
            continue
        for item in items:
            published = (
                item.published_at.astimezone().strftime("%m-%d %H:%M")
                if item.published_at
                else "未知时间"
            )
            typer.echo(f"[{item.content_type.value}] {published} {item.title}")
            typer.echo(f"    {item.url}")
        typer.secho(f"共 {len(items)} 条", fg=typer.colors.GREEN)

    if failed:
        raise typer.Exit(code=1)


@app.command()
def archive(
    year: int = typer.Option(
        ..., "--year", "-y", help="归档起始年份，例如 2026 表示抓 2026 年至今"
    ),
    max_scrolls: int = typer.Option(
        300, "--max-scrolls", help="回溯专用滚动上限（动态多时需要更大值）"
    ),
    detail: bool = typer.Option(
        True, "--detail/--no-detail", help="归档后顺带回填图文动态详情（默认开启）"
    ),
) -> None:
    """回溯归档：抓取 year 年 1 月 1 日以来的全部动态，按「UID-名称/时间」写本地 JSON。

    只写本地 data/archive/，不上传任何远程；已存在的文件自动跳过，可反复补抓。
    默认归档后顺带回填缺正文/图片的图文动态详情（同一浏览器会话策略，人类节奏）。
    """
    runtime = _load_or_exit()
    configure_logging(runtime.app.log_level)
    # 回溯场景放宽滚动/响应上限（日常增量的风控基线不变），人类节奏保持不变
    runtime.app.browser.max_scrolls = max_scrolls
    runtime.app.browser.max_feed_responses = max_scrolls
    since = datetime(year, 1, 1, tzinfo=UTC)
    typer.secho(f"archive：归档 {year} 年至今的动态（since={since.date()}）", fg=typer.colors.CYAN)

    failed = False
    for connector in build_connectors(runtime):
        name = connector.source.name or connector.source.uid
        typer.echo(f"\n== {name}（uid={connector.source.uid}）==")
        try:
            items = connector.list_updates(since)
        except LoginRequiredError as exc:
            typer.secho(f"[FAIL] {exc}，请先执行 opinion-radar bilibili-login", fg=typer.colors.RED)
            failed = True
            continue
        except RiskControlBlocked as exc:
            typer.secho(f"[FAIL] 命中风控已熔断：{exc}", fg=typer.colors.RED)
            failed = True
            continue
        except (BrowserRuntimeMissingError, CollectorError) as exc:
            typer.secho(f"[FAIL] 采集失败：{exc}", fg=typer.colors.RED)
            failed = True
            continue

        written, skipped = archive_items(items, uid=connector.source.uid)
        typer.secho(
            f"归档完成：新写入 {len(written)} 条，跳过已存在 {skipped} 条", fg=typer.colors.GREEN
        )
        if detail:
            if _backfill_connector_details(connector, limit=None):
                failed = True
            if _backfill_connector_videos(connector, limit=None):
                failed = True

    if failed:
        raise typer.Exit(code=1)


def _backfill_connector_details(connector: Connector, limit: int | None) -> bool:
    """回填某博主归档中缺正文/图片的图文动态详情，返回是否失败。

    与 backfill-detail 命令共用同一套逻辑；归档顺带调用时 limit=None 全量回填。
    """
    uid = connector.source.uid
    pending = [(path, item) for path, item in load_archive_items(uid) if needs_detail(item)]
    if not pending:
        typer.secho("归档中没有缺详情的图文动态", fg=typer.colors.YELLOW)
        return False
    if limit is not None:
        pending = pending[:limit]
    typer.echo(f"待回填 {len(pending)} 条，将逐条打开动态详情页（人类节奏）…")

    try:
        details = connector.fetch_details([item for _, item in pending])
    except LoginRequiredError as exc:
        typer.secho(f"[FAIL] {exc}，请先执行 opinion-radar bilibili-login", fg=typer.colors.RED)
        return True
    except RiskControlBlocked as exc:
        typer.secho(f"[FAIL] 命中风控已熔断：{exc}", fg=typer.colors.RED)
        return True
    except (BrowserRuntimeMissingError, CollectorError) as exc:
        typer.secho(f"[FAIL] 采集失败：{exc}", fg=typer.colors.RED)
        return True

    written = 0
    img_ok = img_failed = 0
    for path, item in pending:
        dynamic_id = str(item.payload.get("dynamic_id") or item.external_id)
        detail = details.get(dynamic_id)
        if detail is None:
            typer.secho(f"[WARN] 详情未获取到，跳过：{dynamic_id}", fg=typer.colors.YELLOW)
            continue
        merged = merge_detail(item, detail)
        overwrite_archive_item(path, merged)
        ok, _, img_fail = download_item_images(merged, archive_dir=path.parent)
        img_ok += ok
        img_failed += img_fail
        written += 1
    typer.secho(
        f"回填完成：更新 {written} 条，未获取 {len(pending) - written} 条，"
        f"图片下载 {img_ok} 张（失败 {img_failed} 张）",
        fg=typer.colors.GREEN,
    )
    return False


@app.command("backfill-detail")
def backfill_detail(
    limit: int | None = typer.Option(
        None, "--limit", help="本轮最多回填条数（风控友好，可分批执行）"
    ),
    headed: bool = typer.Option(False, "--headed", help="有头模式运行，便于观察页面与人工介入"),
) -> None:
    """回填归档中缺正文 / 图片的图文动态详情（详情页拦截，只写本地 data/archive/）。"""
    runtime = _load_or_exit()
    configure_logging(runtime.app.log_level)
    if headed:
        runtime.app.browser.headless = False

    failed = False
    for connector in build_connectors(runtime):
        uid = connector.source.uid
        name = connector.source.name or uid
        typer.echo(f"\n== {name}（uid={uid}）==")
        if _backfill_connector_details(connector, limit=limit):
            failed = True

    if failed:
        raise typer.Exit(code=1)


def _backfill_connector_videos(connector: Connector, limit: int | None) -> bool:
    """回填某博主归档中缺字幕正文的视频，返回是否失败。

    与 backfill-subtitle 命令共用同一套逻辑；归档顺带调用时 limit=None 全量回填。
    未付费的充电专属视频会被标记「付费不可达」并跳过，不再进入待回填列表。

    分批执行（每批换新浏览器会话、逐批落盘）：headless Chromium 长会话偶发
    停止响应 CDP 导致后续调用永久挂起（实测约 10+ 页后），小批次 + 幂等重跑
    把单次挂起的损失限制在一批之内。
    """
    uid = connector.source.uid
    pending = [(path, item) for path, item in load_archive_items(uid) if needs_video_detail(item)]
    if not pending:
        typer.secho("归档中没有缺字幕的视频", fg=typer.colors.YELLOW)
        return False
    if limit is not None:
        pending = pending[:limit]
    typer.echo(f"待回填 {len(pending)} 条视频，分批打开播放页（每批 {_VIDEO_BATCH_SIZE} 条）…")

    failed = False
    for start in range(0, len(pending), _VIDEO_BATCH_SIZE):
        batch = pending[start : start + _VIDEO_BATCH_SIZE]
        typer.echo(f"-- 第 {start // _VIDEO_BATCH_SIZE + 1} 批（{len(batch)} 条）")
        try:
            details = connector.fetch_video_details([item for _, item in batch])
        except LoginRequiredError as exc:
            typer.secho(f"[FAIL] {exc}，请先执行 opinion-radar bilibili-login", fg=typer.colors.RED)
            return True
        except RiskControlBlocked as exc:
            typer.secho(f"[FAIL] 命中风控已熔断：{exc}", fg=typer.colors.RED)
            return True
        except (BrowserRuntimeMissingError, CollectorError) as exc:
            typer.secho(f"[FAIL] 采集失败：{exc}", fg=typer.colors.RED)
            return True

        written = 0
        for path, item in batch:
            detail = details.get(item.external_id)
            if detail is None:
                typer.secho(
                    f"[WARN] 字幕未获取到，跳过：{item.external_id}", fg=typer.colors.YELLOW
                )
                continue
            merged = merge_video_detail(item, detail)
            overwrite_archive_item(path, merged)
            status = merged.payload.get("subtitle_status", "ok")
            body_len = len((merged.body_text or "").strip())
            typer.echo(f"    {item.external_id} 状态={status} 正文 {body_len} 字")
            written += 1
        typer.secho(f"本批完成：更新 {written} 条", fg=typer.colors.GREEN)
        if written < len(batch):
            failed = True
    return failed


@app.command("backfill-subtitle")
def backfill_subtitle(
    limit: int | None = typer.Option(
        None, "--limit", help="本轮最多回填条数（风控友好，可分批执行）"
    ),
    headed: bool = typer.Option(False, "--headed", help="有头模式运行，便于观察页面与人工介入"),
) -> None:
    """回填归档中缺字幕正文的视频（播放页拦截 player 接口，只写本地 data/archive/）。

    仅提取字幕文字，不保存任何视频文件；未付费的充电专属视频标记「付费不可达」。
    """
    runtime = _load_or_exit()
    configure_logging(runtime.app.log_level)
    if headed:
        runtime.app.browser.headless = False

    failed = False
    for connector in build_connectors(runtime):
        uid = connector.source.uid
        name = connector.source.name or uid
        typer.echo(f"\n== {name}（uid={uid}）==")
        if _backfill_connector_videos(connector, limit=limit):
            failed = True

    if failed:
        raise typer.Exit(code=1)


def _backfill_connector_comments(connector: Connector, limit: int | None) -> bool:
    """回填某博主归档中缺评论区 UP 主回复的条目，返回是否失败。

    与 backfill-comments 命令共用同一套逻辑。分批执行（每批换新浏览器会话、
    逐批落盘）；评论段落按幂等规则拼入已有正文（merge_comments）。
    """
    uid = connector.source.uid
    pending = [(path, item) for path, item in load_archive_items(uid) if needs_comments(item)]
    if not pending:
        typer.secho("归档中没有缺评论的条目", fg=typer.colors.YELLOW)
        return False
    if limit is not None:
        pending = pending[:limit]
    typer.echo(f"待回填 {len(pending)} 条评论，分批打开页面（每批 {_COMMENT_BATCH_SIZE} 条）…")

    failed = False
    for start in range(0, len(pending), _COMMENT_BATCH_SIZE):
        batch = pending[start : start + _COMMENT_BATCH_SIZE]
        typer.echo(f"-- 第 {start // _COMMENT_BATCH_SIZE + 1} 批（{len(batch)} 条）")
        try:
            details = connector.fetch_comments([item for _, item in batch])
        except LoginRequiredError as exc:
            typer.secho(f"[FAIL] {exc}，请先执行 opinion-radar bilibili-login", fg=typer.colors.RED)
            return True
        except RiskControlBlocked as exc:
            typer.secho(f"[FAIL] 命中风控已熔断：{exc}", fg=typer.colors.RED)
            return True
        except (BrowserRuntimeMissingError, CollectorError) as exc:
            typer.secho(f"[FAIL] 采集失败：{exc}", fg=typer.colors.RED)
            return True

        for path, item in batch:
            detail = details.get(item.external_id)
            if detail is None:
                typer.secho(
                    f"[WARN] 评论未获取到，跳过：{item.external_id}", fg=typer.colors.YELLOW
                )
                failed = True
                continue
            merged = merge_comments(item, detail)
            overwrite_archive_item(path, merged)
            status = merged.payload.get("comment_status", "?")
            count = merged.payload.get("up_reply_count", 0)
            typer.echo(f"    {item.external_id} 状态={status} UP主回复 {count} 条")
        typer.secho("本批完成", fg=typer.colors.GREEN)
    return failed


@app.command("backfill-comments")
def backfill_comments(
    limit: int | None = typer.Option(
        None, "--limit", help="本轮最多回填条数（风控友好，可分批执行）"
    ),
    headed: bool = typer.Option(False, "--headed", help="有头模式运行，便于观察页面与人工介入"),
) -> None:
    """回填归档中缺评论区 UP 主回复的条目（拼入正文，只写本地 data/archive/）。

    只提取 UP 主本人的回复文本与被回复评论的文本上下文，不采集粉丝昵称等信息；
    未付费的充电专属视频跳过评论采集。
    """
    runtime = _load_or_exit()
    configure_logging(runtime.app.log_level)
    if headed:
        runtime.app.browser.headless = False

    failed = False
    for connector in build_connectors(runtime):
        uid = connector.source.uid
        name = connector.source.name or uid
        typer.echo(f"\n== {name}（uid={uid}）==")
        if _backfill_connector_comments(connector, limit=limit):
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
