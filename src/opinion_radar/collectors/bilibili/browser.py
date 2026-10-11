"""Playwright 真实浏览器会话（决策见 ADR-0005）。

只驱动真人会做的动作（打开页面、慢速滚动），在响应事件里原样截获接口 JSON；
不自行签名、不直接调用私有接口。风控基线：
- 持久化 profile + 专用小号登录，单实例、绝不并发；
- 动作间隔随机 2~8 秒，滚动次数 / feed 响应数有硬上限；
- 命中验证码 / 风控码 / 登录失效立即熔断，冷却 24h，不重试硬闯。
"""

from __future__ import annotations

import random
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from opinion_radar.collectors.bilibili.feed import FEED_API_MARKER, FeedPage, parse_feed_page
from opinion_radar.collectors.exceptions import (
    BrowserRuntimeMissingError,
    CollectorError,
    CollectorTimeout,
    LoginRequiredError,
    RiskControlBlocked,
)
from opinion_radar.config import BrowserConfig
from opinion_radar.domain.models import ContentItem

SPACE_DYNAMIC_URL = "https://space.bilibili.com/{uid}/dynamic"
LOGIN_URL = "https://passport.bilibili.com/login"
_SESSDATA_COOKIE = "SESSDATA"
# 页面上的验证码 / 风控弹层特征
_CAPTCHA_SELECTOR = ".geetest_holder, .geetest_panel, #geetest-wrap"
_COOLDOWN_FILENAME = ".bilibili_blocked_until"
# 连续遇到多少条早于 since 的非置顶动态即停止翻页（防置顶动态误停）
_OLD_STOP_THRESHOLD = 2
# 连续多少轮滚动没有新响应即判定已到底
_IDLE_STOP_THRESHOLD = 2


class BilibiliBrowser:
    """单个 B 站浏览器会话，用法：``with BilibiliBrowser(cfg) as b: b.list_dynamics(...)``。"""

    def __init__(self, config: BrowserConfig, *, headless: bool | None = None) -> None:
        self.config = config
        # 登录命令强制有头；其他场景默认读配置
        self.headless = config.headless if headless is None else headless
        self._playwright: Any = None
        self._context: Any = None

    def __enter__(self) -> BilibiliBrowser:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def start(self) -> None:
        """启动持久化浏览器上下文（profile 不存在时自动创建）。"""
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserRuntimeMissingError(
                "未安装 playwright：请执行 uv sync --extra browser "
                "&& uv run playwright install chromium"
            ) from exc

        profile_dir = self.config.profile_dir
        profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=self.headless,
                channel=self.config.channel,
                viewport={"width": 1440, "height": 900},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-default-browser-check",
                ],
            )
        except PlaywrightError as exc:
            if "Executable doesn't exist" in str(exc):
                hint = (
                    "未找到 Google Chrome：请安装 Chrome，或将 browser.channel 改回 null"
                    if self.config.channel
                    else "未安装 Chromium 运行时：请执行 uv run playwright install chromium"
                )
                raise BrowserRuntimeMissingError(hint) from exc
            raise
        self._context.set_default_navigation_timeout(
            int(self.config.navigation_timeout_seconds * 1000)
        )
        logger.info(f"browser started profile={profile_dir} headless={self.headless}")

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def is_logged_in(self) -> bool:
        """profile 中是否存在有效的 SESSDATA cookie。"""
        context = self._require_context()
        cookies = context.cookies(["https://www.bilibili.com/", "https://space.bilibili.com/"])
        return any(c.get("name") == _SESSDATA_COOKIE and c.get("value") for c in cookies)

    def wait_for_login(self) -> bool:
        """有头模式打开登录页，轮询等待扫码登录完成（命令行入口保证 headless=False）。"""
        context = self._require_context()
        page = context.new_page()
        logger.info("opening bilibili login page, waiting for scan login")
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
        except PlaywrightTimeoutError as exc:
            raise CollectorTimeout("打开 B 站登录页超时，请检查网络后重试") from exc
        except PlaywrightError as exc:
            raise CollectorError(f"打开 B 站登录页失败：{exc}") from exc
        deadline = time.monotonic() + self.config.login_timeout_seconds
        try:
            while time.monotonic() < deadline:
                if self.is_logged_in():
                    return True
                page.wait_for_timeout(2000)
            return False
        finally:
            page.close()

    def list_dynamics(self, uid: str, since: datetime) -> list[ContentItem]:
        """打开空间动态页并慢速滚动，拦截动态流响应，返回 since 之后的去重内容。"""
        self._enforce_cooldown()
        context = self._require_context()
        if not self.is_logged_in():
            raise LoginRequiredError("浏览器 profile 未登录：请先执行 opinion-radar bilibili-login")

        page = context.new_page()
        captured: list[dict[str, Any]] = []

        def _on_response(response: Any) -> None:
            if FEED_API_MARKER not in response.url:
                return
            try:
                payload = response.json()
            except Exception as exc:  # 跳转/中止的响应可能拿不到 body，忽略即可
                logger.debug(f"feed response unreadable: {exc}")
                return
            captured.append(payload)

        page.on("response", _on_response)
        url = SPACE_DYNAMIC_URL.format(uid=uid)
        logger.info(f"dynamic_feed_start uid={uid} since={since.isoformat()}")
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        try:
            try:
                page.goto(url, wait_until="domcontentloaded")
            except PlaywrightTimeoutError as exc:
                raise CollectorTimeout(f"打开空间页超时：{url}") from exc
            except PlaywrightError as exc:
                raise CollectorError(f"浏览器打开空间页失败：{exc}") from exc
            timeout = self.config.feed_response_timeout_seconds
            if not self._wait_captures(page, captured, 0, timeout):
                raise CollectorTimeout(f"打开空间页后 {timeout}s 内未拦截到动态流响应")
            items = self._scroll_and_collect(page, captured, uid, since)
        except RiskControlBlocked:
            self._write_cooldown()
            raise
        finally:
            page.close()

        items.sort(key=lambda item: item.published_at or datetime.min.replace(tzinfo=UTC))
        logger.info(
            f"dynamic_feed_done uid={uid} new_items={len(items)} feed_responses={len(captured)}"
        )
        return items

    def _scroll_and_collect(
        self,
        page: Any,
        captured: list[dict[str, Any]],
        uid: str,
        since: datetime,
    ) -> list[ContentItem]:
        """边滚动边解析新拦截到的响应，直到早于 since / 无更多 / 触顶任一上限。"""
        cursor = 0
        seen_dynamic_ids: set[str] = set()
        seen_external_ids: set[str] = set()
        collected: list[ContentItem] = []
        consecutive_old = 0
        idle_rounds = 0
        scrolls = 0
        last_feed: FeedPage | None = None

        while True:
            # 解析自上次以来新拦截到的所有响应（解析即做业务码检查，风控在此抛出）
            while cursor < len(captured):
                payload = captured[cursor]
                cursor += 1
                last_feed = parse_feed_page(payload, source_uid=uid)
                for item in last_feed.items:
                    dynamic_id = str(item.payload.get("dynamic_id") or item.external_id)
                    if dynamic_id in seen_dynamic_ids:
                        continue
                    seen_dynamic_ids.add(dynamic_id)
                    if self._is_before_since(item, since) and not item.payload.get("is_pinned"):
                        consecutive_old += 1
                        continue
                    if item.external_id in seen_external_ids:
                        continue
                    seen_external_ids.add(item.external_id)
                    consecutive_old = 0
                    collected.append(item)

            self._raise_if_blocked(page)

            if consecutive_old >= _OLD_STOP_THRESHOLD:
                logger.info("feed stop: reached items older than since")
                break
            if last_feed is not None and not last_feed.has_more:
                logger.info("feed stop: has_more=false")
                break
            if len(captured) >= self.config.max_feed_responses:
                logger.warning(f"feed stop: max_feed_responses={self.config.max_feed_responses}")
                break
            if scrolls >= self.config.max_scrolls:
                logger.warning(f"feed stop: max_scrolls={self.config.max_scrolls}")
                break

            baseline = len(captured)
            self._human_pause(
                page, self.config.action_delay_min_seconds, self.config.action_delay_max_seconds
            )
            page.mouse.wheel(0, random.randint(1200, 3200))
            scrolls += 1
            self._human_pause(
                page, self.config.scroll_wait_min_seconds, self.config.scroll_wait_max_seconds
            )
            if self._wait_captures(page, captured, baseline, timeout_seconds=4.0):
                idle_rounds = 0
            else:
                idle_rounds += 1
                if idle_rounds >= _IDLE_STOP_THRESHOLD:
                    logger.info("feed stop: no new responses after scrolling")
                    break

        return collected

    def _raise_if_blocked(self, page: Any) -> None:
        """检查登录跳转与验证码弹层，命中即抛风控 / 登录异常。"""
        current_url = str(page.url or "")
        if "passport.bilibili.com" in current_url and "/login" in current_url:
            raise LoginRequiredError("被重定向到登录页：登录态已失效，请重新执行 bilibili-login")
        try:
            captcha = page.query_selector(_CAPTCHA_SELECTOR)
        except Exception as exc:  # 页面正在跳转时查询可能失败，不影响下一轮检查
            logger.debug(f"captcha query failed: {exc}")
            return
        if captcha is not None:
            raise RiskControlBlocked("页面出现验证码弹层：本轮熔断，不重试")

    def _enforce_cooldown(self) -> None:
        marker = self._cooldown_path()
        if not marker.exists():
            return
        try:
            until = datetime.fromisoformat(marker.read_text(encoding="utf-8").strip())
        except ValueError:
            marker.unlink(missing_ok=True)
            return
        now = datetime.now(UTC)
        if now < until:
            raise RiskControlBlocked(f"风控冷却中，解除时间 {until.isoformat()}，期间不启动采集")
        marker.unlink(missing_ok=True)

    def _write_cooldown(self) -> None:
        until = datetime.now(UTC) + timedelta(minutes=self.config.block_cooldown_minutes)
        marker = self._cooldown_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(until.isoformat(), encoding="utf-8")
        logger.warning(f"risk control cooldown written until={until.isoformat()} path={marker}")

    def _cooldown_path(self) -> Path:
        return self.config.profile_dir.parent / _COOLDOWN_FILENAME

    def _human_pause(self, page: Any, min_seconds: float, max_seconds: float) -> None:
        # 必须用 page.wait_for_timeout 而非 time.sleep：
        # Playwright 同步 API 的事件分发跑在当前线程，time.sleep 会阻塞它，
        # 导致 response 事件积压、响应体拉取挂起（曾造成 feed 永远拦截不到）。
        page.wait_for_timeout(random.uniform(min_seconds, max_seconds) * 1000)

    def _wait_captures(
        self,
        page: Any,
        captured: list[dict[str, Any]],
        baseline: int,
        timeout_seconds: float,
    ) -> bool:
        """等待拦截到比 baseline 更新的响应（用 wait_for_timeout 保持事件分发）。"""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if len(captured) > baseline:
                return True
            page.wait_for_timeout(500)
        return len(captured) > baseline

    @staticmethod
    def _is_before_since(item: ContentItem, since: datetime) -> bool:
        return item.published_at is not None and item.published_at <= since

    def _require_context(self) -> Any:
        if self._context is None:
            raise BrowserRuntimeMissingError("浏览器会话尚未 start()")
        return self._context
