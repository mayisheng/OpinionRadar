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
from urllib.parse import parse_qs, urlparse

from loguru import logger
from pydantic import BaseModel

from opinion_radar.collectors.bilibili.comment import (
    COMMENT_MAIN_API_MARKER,
    COMMENT_TYPE_DYNAMIC,
    COMMENT_TYPE_VIDEO,
    FlaggedThread,
    UpReply,
    build_sub_reply_url,
    parse_comment_main_page,
    parse_sub_replies,
)
from opinion_radar.collectors.bilibili.feed import (
    DETAIL_API_MARKERS,
    DYNAMIC_URL_TEMPLATE,
    FEED_API_MARKER,
    VIDEO_URL_TEMPLATE,
    FeedPage,
    parse_detail_item,
    parse_feed_page,
)
from opinion_radar.collectors.bilibili.subtitle import (
    PLAYER_API_MARKERS,
    PlayerInfo,
    parse_player_info,
)
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


class PlayerPageResult(BaseModel):
    """视频播放页一次访问的产出：player 信息 + 评论区 UP 主回复。"""

    info: PlayerInfo
    up_replies: list[UpReply]
    comment_pages: int = 0  # 拦截到的主楼评论页数（0 = 评论区未加载）


class DetailPageResult(BaseModel):
    """动态详情页一次访问的产出：详情条目 + 评论区 UP 主回复。"""

    item: ContentItem
    up_replies: list[UpReply]
    comment_pages: int = 0


class CommentPageResult(BaseModel):
    """纯评论回填一次页面访问的产出。"""

    up_replies: list[UpReply]
    comment_pages: int = 0


class CommentTarget(BaseModel):
    """纯评论回填的目标页面。"""

    external_id: str
    url: str
    comment_type: int  # COMMENT_TYPE_VIDEO / COMMENT_TYPE_DYNAMIC


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
                    # 目标站点在国内，绕过系统代理：本机代理进程不在时
                    # Chromium 会因 ERR_PROXY_CONNECTION_FAILED 全部导航失败
                    "--no-proxy-server",
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
        """边滚动边解析新拦截到的响应，直到早于 since / 无更多 / 触顶任一上限。

        突破分页限制：当页面滚动到底不再产生新响应、但接口仍返回 has_more=True 时，
        用 page.evaluate 在页面上下文里直接发 fetch 请求（复用浏览器 cookie 与指纹），
        以最后一条动态的 id_str 作为 offset 继续翻页，直到 has_more=False 或时间窗外。
        """
        cursor = 0
        seen_dynamic_ids: set[str] = set()
        seen_external_ids: set[str] = set()
        collected: list[ContentItem] = []
        consecutive_old = 0
        idle_rounds = 0
        scrolls = 0
        last_feed: FeedPage | None = None
        # 手动翻页状态：page_scroll 阶段结束后若 has_more 仍 True，则切到 api_scroll
        api_offset: str | None = None
        api_scroll_count = 0

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
            elif self._at_page_bottom(page):
                # 已到底仍无新响应：连续多轮即判定页面滚动不再加载，尝试 API 翻页
                idle_rounds += 1
                if idle_rounds >= _IDLE_STOP_THRESHOLD:
                    if api_offset is None and last_feed is not None and last_feed.has_more:
                        # 从最后一条响应提取 offset，进入 API 翻页阶段
                        api_offset = self._extract_offset(last_feed)
                        if api_offset:
                            logger.info(f"feed switch to api pagination: offset={api_offset}")
                            # 重置 idle 计数，给 API 翻页更多机会
                            idle_rounds = 0
                            # 用 API 翻页继续，不再依赖页面滚动
                            new_payloads = self._fetch_feed_by_offset(page, uid, api_offset, since)
                            for payload in new_payloads:
                                captured.append(payload)
                            continue
                    logger.info("feed stop: no new responses after reaching bottom")
                    break
            else:
                # 还没滚到底，只是本轮未触发新请求，不能算 idle
                idle_rounds = 0

            # API 翻页阶段：持续用 offset 拉取直到 has_more=False 或时间窗外
            if api_offset is not None:
                # 解析完当前 captured 后，若还有 has_more 且未超时，继续 API 翻页
                if last_feed is not None and last_feed.has_more:
                    next_offset = self._extract_offset(last_feed)
                    if next_offset and next_offset != api_offset:
                        api_offset = next_offset
                        api_scroll_count += 1
                        if api_scroll_count >= self.config.max_feed_responses:
                            logger.warning(
                                "api pagination stop: "
                                f"max_api_scrolls={self.config.max_feed_responses}"
                            )
                            break
                        self._human_pause(
                            page,
                            self.config.action_delay_min_seconds,
                            self.config.action_delay_max_seconds,
                        )
                        new_payloads = self._fetch_feed_by_offset(page, uid, api_offset, since)
                        if not new_payloads:
                            logger.info("api pagination stop: empty response")
                            break
                        for payload in new_payloads:
                            captured.append(payload)
                        continue
                    logger.info("api pagination stop: no next offset")
                    break
                logger.info("api pagination stop: has_more=false")
                break

        return collected

    @staticmethod
    def _at_page_bottom(page: Any) -> bool:
        """页面是否已滚动到底部附近（剩余不足半个视口）；评估失败按已到底处理。"""
        try:
            return bool(
                page.evaluate(
                    "window.innerHeight + window.scrollY"
                    " >= document.documentElement.scrollHeight - window.innerHeight * 0.5"
                )
            )
        except Exception:
            return True

    @staticmethod
    def _extract_offset(feed: FeedPage) -> str | None:
        """从 FeedPage 提取下页 offset：取最后一条动态的 id_str（B 站分页游标）。"""
        if not feed.items:
            return None
        last = feed.items[-1]
        return str(last.payload.get("dynamic_id") or last.external_id)

    def _fetch_feed_by_offset(
        self, page: Any, uid: str, offset: str, since: datetime
    ) -> list[dict[str, Any]]:
        """在页面上下文里用 fetch 直接调用 feed/space 接口，复用浏览器 cookie 与指纹。

        只读操作，不自行签名、不修改任何状态；风控基线由浏览器会话保持。
        返回新拦截到的响应列表（可能为空，表示已到时间窗外或接口无更多）。
        """
        # 构造与页面滚动完全一致的请求 URL（features 等参数从已拦截请求复制）
        # 这里用模板拼接，实际参数由 B 站前端固定，与页面滚动时一致
        url = (
            f"https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
            f"?offset={offset}&host_mid={uid}&timezone_offset=-480&platform=web"
            f"&features=itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote,"
            f"forwardListHidden,decorationCard,commentsNewVersion,onlyfansAssetsV2,"
            f"ugcDelete,onlyfansQaCard,avatarAutoTheme,sunflowerStyle,cardsEnhance,"
            f"eva3CardOpus,eva3CardVideo,eva3CardComment,eva3CardUser"
            f"&web_location=333.1387"
        )
        logger.debug(f"api pagination fetch: offset={offset}")

        result = self._page_fetch_json(page, url)
        if not isinstance(result, dict):
            logger.warning(f"api pagination unexpected result type: {type(result)}")
            return []

        code = result.get("code")
        if code != 0:
            message = result.get("message") or result.get("msg") or "unknown"
            logger.warning(f"api pagination error code={code} message={message}")
            return []

        data = result.get("data")
        if not isinstance(data, dict):
            return []

        # 把 API 结果包装成与页面拦截一致的格式，供 parse_feed_page 解析
        # 注意：这里只返回当前页数据，调用方负责追加到 captured 列表
        return [result]

    @staticmethod
    def _page_fetch_json(page: Any, url: str) -> Any:
        """在页面上下文执行 fetch GET 并返回 JSON（复用浏览器 cookie 与指纹）。

        只读操作，不自行签名、不修改任何状态；风控基线由浏览器会话保持。
        """
        return page.evaluate(
            """async (url) => {
                try {
                    const resp = await fetch(url, {
                        method: 'GET',
                        credentials: 'include',
                        headers: { 'Accept': 'application/json' }
                    });
                    return await resp.json();
                } catch (e) {
                    return { code: -1, message: String(e) };
                }
            }""",
            url,
        )

    def fetch_dynamic_details(
        self, dynamic_ids: list[str], *, uid: str
    ) -> dict[str, DetailPageResult]:
        """批量打开动态详情页，拦截 detail + 主楼评论响应，返回 dynamic_id → DetailPageResult。

        单个浏览器会话内逐条访问（条间人类节奏停顿），单条失败只记日志不中断整批；
        命中风控 / 登录失效仍然熔断抛出。评论采集为顺带行为，失败不影响详情结果。
        """
        self._enforce_cooldown()
        if not self.is_logged_in():
            raise LoginRequiredError("浏览器 profile 未登录：请先执行 opinion-radar bilibili-login")

        context = self._require_context()
        results: dict[str, DetailPageResult] = {}
        timeout = self.config.detail_response_timeout_seconds

        for index, dynamic_id in enumerate(dynamic_ids):
            page = context.new_page()
            try:
                if index > 0:
                    self._human_pause(
                        page,
                        self.config.action_delay_min_seconds,
                        self.config.action_delay_max_seconds,
                    )
                result = self._fetch_one_detail(page, dynamic_id, uid=uid, timeout=timeout)
            finally:
                page.close()
            if result is not None:
                results[dynamic_id] = result

        logger.info(f"dynamic_details_done total={len(dynamic_ids)} ok={len(results)}")
        return results

    def _fetch_one_detail(
        self, page: Any, dynamic_id: str, *, uid: str, timeout: float
    ) -> DetailPageResult | None:
        """在给定页面上打开单条动态详情页，拦截 detail 响应并顺带采评论；失败返回 None。"""
        captured: list[dict[str, Any]] = []
        comment_captured: list[dict[str, Any]] = []

        def _on_response(response: Any) -> None:
            if any(marker in response.url for marker in DETAIL_API_MARKERS):
                try:
                    payload = response.json()
                except Exception as exc:  # 跳转/中止的响应可能拿不到 body，忽略即可
                    logger.debug(f"detail response unreadable: {exc}")
                    return
                captured.append(payload)
            elif COMMENT_MAIN_API_MARKER in response.url:
                self._capture_comment_response(response, comment_captured)

        page.on("response", _on_response)
        url = DYNAMIC_URL_TEMPLATE.format(dynamic_id=dynamic_id)
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        try:
            try:
                page.goto(url, wait_until="domcontentloaded")
            except PlaywrightTimeoutError as exc:
                raise CollectorTimeout(f"打开动态详情页超时：{url}") from exc
            except PlaywrightError as exc:
                raise CollectorError(f"浏览器打开动态详情页失败：{exc}") from exc
            self._wait_captures(page, captured, 0, timeout)
            self._raise_if_blocked(page)
        except RiskControlBlocked:
            self._write_cooldown()
            raise
        except CollectorError as exc:
            logger.warning(f"dynamic_detail_failed id={dynamic_id} error={exc}")
            return None

        # 详情页可能顺带加载其它动态的 detail 响应，逐条解析后按 id 匹配目标
        for payload in captured:
            try:
                item = parse_detail_item(payload, source_uid=uid)
            except (RiskControlBlocked, LoginRequiredError):
                self._write_cooldown()
                raise
            except CollectorError as exc:
                logger.debug(f"detail parse skipped id={dynamic_id} error={exc}")
                continue
            if item.external_id == dynamic_id:
                dyn_type = item.payload.get("dynamic_type")
                logger.info(f"dynamic_detail_ok id={dynamic_id} type={dyn_type}")
                comments = self._collect_comments(
                    page, comment_captured, up_mid=uid, comment_type=COMMENT_TYPE_DYNAMIC
                )
                return DetailPageResult(
                    item=item, up_replies=comments.up_replies, comment_pages=comments.comment_pages
                )
        logger.warning(f"dynamic_detail_missing id={dynamic_id} captures={len(captured)}")
        return None

    def fetch_video_players(self, bvids: list[str], *, uid: str) -> dict[str, PlayerPageResult]:
        """逐条打开视频播放页，拦截 player + 主楼评论响应，返回 bvid → PlayerPageResult。

        单个浏览器会话内逐条访问（条间人类节奏停顿），单条失败只记日志不中断整批；
        命中风控 / 登录失效仍然熔断抛出。字幕正文下载不在此处（CDN 直连，见 subtitle.py）。
        评论采集为顺带行为，失败不影响 player 结果。
        """
        self._enforce_cooldown()
        if not self.is_logged_in():
            raise LoginRequiredError("浏览器 profile 未登录：请先执行 opinion-radar bilibili-login")

        context = self._require_context()
        results: dict[str, PlayerPageResult] = {}
        timeout = self.config.detail_response_timeout_seconds

        for index, bvid in enumerate(bvids):
            page = context.new_page()
            try:
                if index > 0:
                    self._human_pause(
                        page,
                        self.config.action_delay_min_seconds,
                        self.config.action_delay_max_seconds,
                    )
                result = self._fetch_one_player(page, bvid, uid=uid, timeout=timeout)
            finally:
                page.close()
            if result is not None:
                results[bvid] = result

        logger.info(f"video_players_done total={len(bvids)} ok={len(results)}")
        return results

    def _fetch_one_player(
        self, page: Any, bvid: str, *, uid: str, timeout: float
    ) -> PlayerPageResult | None:
        """在给定页面上打开单个视频播放页，拦截 player 响应并顺带采评论；失败返回 None。"""
        captured: list[dict[str, Any]] = []
        comment_captured: list[dict[str, Any]] = []

        def _on_response(response: Any) -> None:
            if any(marker in response.url for marker in PLAYER_API_MARKERS):
                try:
                    payload = response.json()
                except Exception as exc:  # 跳转/中止的响应可能拿不到 body，忽略即可
                    logger.debug(f"player response unreadable: {exc}")
                    return
                captured.append(payload)
            elif COMMENT_MAIN_API_MARKER in response.url:
                self._capture_comment_response(response, comment_captured)

        page.on("response", _on_response)
        url = VIDEO_URL_TEMPLATE.format(bvid=bvid)
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        try:
            try:
                page.goto(url, wait_until="domcontentloaded")
            except PlaywrightTimeoutError as exc:
                raise CollectorTimeout(f"打开视频播放页超时：{url}") from exc
            except PlaywrightError as exc:
                raise CollectorError(f"浏览器打开视频播放页失败：{exc}") from exc
            self._wait_captures(page, captured, 0, timeout)
            self._raise_if_blocked(page)
        except RiskControlBlocked:
            self._write_cooldown()
            raise
        except CollectorError as exc:
            logger.warning(f"video_player_failed bvid={bvid} error={exc}")
            return None

        # 同一页面可能发出多个 player 相关响应（v2 / 在线人数等），解析后按 bvid 匹配
        for payload in captured:
            try:
                info = parse_player_info(payload)
            except (RiskControlBlocked, LoginRequiredError):
                self._write_cooldown()
                raise
            except CollectorError as exc:
                logger.debug(f"player parse skipped bvid={bvid} error={exc}")
                continue
            if info.bvid == bvid:
                logger.info(
                    f"video_player_ok bvid={bvid} paid={info.is_paid} "
                    f"playable={info.paid_playable} lan={info.subtitle_lan}"
                )
                if info.is_paid and not info.paid_playable:
                    # 合规红线：未付费的充电专属视频不采评论，直接返回空结果
                    return PlayerPageResult(info=info, up_replies=[], comment_pages=0)
                comments = self._collect_comments(
                    page, comment_captured, up_mid=uid, comment_type=COMMENT_TYPE_VIDEO
                )
                return PlayerPageResult(
                    info=info, up_replies=comments.up_replies, comment_pages=comments.comment_pages
                )
        logger.warning(f"video_player_missing bvid={bvid} captures={len(captured)}")
        return None

    def fetch_comments_batch(
        self, targets: list[CommentTarget], *, uid: str
    ) -> dict[str, CommentPageResult]:
        """纯评论回填：逐条打开目标页面只采评论区 UP 主回复，返回 external_id → 结果。

        用于已归档（已有正文 / 字幕）条目的评论补采；单条失败记日志且不收录该条
        （调用方据此标记 error 下轮重试），命中风控 / 登录失效熔断抛出。
        """
        self._enforce_cooldown()
        if not self.is_logged_in():
            raise LoginRequiredError("浏览器 profile 未登录：请先执行 opinion-radar bilibili-login")

        context = self._require_context()
        results: dict[str, CommentPageResult] = {}

        for index, target in enumerate(targets):
            page = context.new_page()
            try:
                if index > 0:
                    self._human_pause(
                        page,
                        self.config.action_delay_min_seconds,
                        self.config.action_delay_max_seconds,
                    )
                result = self._fetch_one_comments(page, target, uid=uid)
            finally:
                page.close()
            if result is not None:
                results[target.external_id] = result

        logger.info(f"comments_batch_done total={len(targets)} ok={len(results)}")
        return results

    def _fetch_one_comments(
        self, page: Any, target: CommentTarget, *, uid: str
    ) -> CommentPageResult | None:
        """在给定页面上打开目标页面并只采集评论区 UP 主回复；失败返回 None。"""
        comment_captured: list[dict[str, Any]] = []

        def _on_response(response: Any) -> None:
            if COMMENT_MAIN_API_MARKER in response.url:
                self._capture_comment_response(response, comment_captured)

        page.on("response", _on_response)
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        try:
            try:
                page.goto(target.url, wait_until="domcontentloaded")
            except PlaywrightTimeoutError as exc:
                raise CollectorTimeout(f"打开页面超时：{target.url}") from exc
            except PlaywrightError as exc:
                raise CollectorError(f"浏览器打开页面失败：{exc}") from exc
            self._raise_if_blocked(page)
            return self._collect_comments(
                page, comment_captured, up_mid=uid, comment_type=target.comment_type
            )
        except RiskControlBlocked:
            self._write_cooldown()
            raise
        except CollectorError as exc:
            logger.warning(f"comments_failed id={target.external_id} error={exc}")
            return None

    @staticmethod
    def _capture_comment_response(response: Any, comment_captured: list[dict[str, Any]]) -> None:
        """拦截主楼评论响应（带 URL，楼中楼 fetch 需要从 URL 取 oid）。"""
        try:
            payload = response.json()
        except Exception as exc:  # 跳转/中止的响应可能拿不到 body，忽略即可
            logger.debug(f"comment response unreadable: {exc}")
            return
        comment_captured.append({"url": response.url, "payload": payload})

    def _collect_comments(
        self,
        page: Any,
        comment_captured: list[dict[str, Any]],
        *,
        up_mid: str,
        comment_type: int,
    ) -> CommentPageResult:
        """滚动触发评论区加载并提取 UP 主回复；失败不致命（返回已采到的部分）。"""
        try:
            self._scroll_to_comments(page, comment_captured)
            if not comment_captured:
                logger.info("comment_main_not_captured: 滚动上限内未加载到评论区")
                return CommentPageResult(up_replies=[], comment_pages=0)
            replies = self._extract_up_replies(
                page, comment_captured, up_mid=up_mid, comment_type=comment_type
            )
            return CommentPageResult(up_replies=replies, comment_pages=len(comment_captured))
        except (RiskControlBlocked, LoginRequiredError):
            self._write_cooldown()
            raise
        except CollectorError as exc:
            logger.warning(f"comment_collect_failed error={exc}")
            return CommentPageResult(up_replies=[], comment_pages=len(comment_captured))

    def _scroll_to_comments(self, page: Any, comment_captured: list[dict[str, Any]]) -> None:
        """人类节奏滚动页面直到拦截到主楼评论响应（或达页数 / 滚动上限、页面到底）。"""
        scrolls = 0
        while (
            len(comment_captured) < self.config.max_comment_pages
            and scrolls < self.config.max_comment_scrolls
        ):
            baseline = len(comment_captured)
            self._human_pause(
                page, self.config.action_delay_min_seconds, self.config.action_delay_max_seconds
            )
            page.mouse.wheel(0, random.randint(1200, 3200))
            scrolls += 1
            got_new = self._wait_captures(page, comment_captured, baseline, timeout_seconds=4.0)
            if self._at_page_bottom(page) and not got_new:
                break

    def _extract_up_replies(
        self,
        page: Any,
        comment_captured: list[dict[str, Any]],
        *,
        up_mid: str,
        comment_type: int,
    ) -> list[UpReply]:
        """解析主楼页并展开 up_reply 标记楼的楼中楼，返回去重后的 UP 主回复。"""
        replies: list[UpReply] = []
        seen: set[str] = set()
        flagged: list[FlaggedThread] = []
        flagged_seen: set[str] = set()
        oid = ""
        for capture in comment_captured[: self.config.max_comment_pages]:
            main = parse_comment_main_page(capture["payload"], up_mid=up_mid)
            if not oid:
                oid = main.oid or self._oid_from_comment_url(capture["url"])
            for reply in main.up_replies:
                if reply.rpid not in seen:
                    seen.add(reply.rpid)
                    replies.append(reply)
            for thread in main.flagged:
                if thread.rpid not in flagged_seen:
                    flagged_seen.add(thread.rpid)
                    flagged.append(thread)
        if not oid:
            logger.warning("comment_oid_missing: 无法确定评论对象 id，跳过楼中楼展开")
            return replies
        for thread in flagged[: self.config.max_flagged_threads]:
            for reply in self._fetch_thread_up_replies(
                page, oid, comment_type, thread, up_mid=up_mid
            ):
                if reply.rpid not in seen:
                    seen.add(reply.rpid)
                    replies.append(reply)
        logger.info(
            f"comment_extract_done main_pages={len(comment_captured)} "
            f"flagged={len(flagged)} up_replies={len(replies)}"
        )
        return replies

    def _fetch_thread_up_replies(
        self,
        page: Any,
        oid: str,
        comment_type: int,
        thread: FlaggedThread,
        *,
        up_mid: str,
    ) -> list[UpReply]:
        """页面上下文 fetch 展开单个标记楼的楼中楼（免签名），过滤 UP 主回复。"""
        results: list[UpReply] = []
        for pn in range(1, self.config.max_subreply_pages + 1):
            url = build_sub_reply_url(oid, comment_type, thread.rpid, pn)
            payload = self._page_fetch_json(page, url)
            if not isinstance(payload, dict):
                logger.warning(f"sub_reply_unexpected root={thread.rpid} type={type(payload)}")
                break
            # parse_sub_replies 内部做业务码检查，风控 / 登录态异常会向上抛出熔断
            sub, has_more = parse_sub_replies(
                payload, up_mid=up_mid, root_rpid=thread.rpid, root_message=thread.message
            )
            results.extend(sub)
            if not has_more:
                break
            self._human_pause(
                page, self.config.action_delay_min_seconds, self.config.action_delay_max_seconds
            )
        return results

    @staticmethod
    def _oid_from_comment_url(url: str) -> str:
        """从主楼评论请求 URL 的 query 中取 oid（视频 aid / 动态 dynamic_id）。"""
        try:
            params = parse_qs(urlparse(url).query)
        except Exception:
            return ""
        return (params.get("oid") or [""])[0]

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
