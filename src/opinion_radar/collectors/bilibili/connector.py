"""B 站采集器：Playwright 真实浏览器响应拦截实现（ADR-0005）。

当前阶段（迭代 001 第四段）：动态流增量发现 + 图文动态详情 + 视频字幕拦截
+ 评论区 UP 主回复抓取（拼入正文，见 comment.py 模块 docstring 的接口结构实录）。
"""

from __future__ import annotations

from datetime import datetime

import httpx
from loguru import logger

from opinion_radar.collectors.base import Connector
from opinion_radar.collectors.bilibili.browser import BilibiliBrowser, CommentTarget
from opinion_radar.collectors.bilibili.comment import (
    COMMENT_EMPTY,
    COMMENT_ERROR,
    COMMENT_OK,
    COMMENT_SKIPPED_PAID,
    COMMENT_TYPE_DYNAMIC,
    COMMENT_TYPE_VIDEO,
    UpReply,
    build_reply_section,
)
from opinion_radar.collectors.bilibili.feed import DYNAMIC_URL_TEMPLATE, VIDEO_URL_TEMPLATE
from opinion_radar.collectors.bilibili.subtitle import (
    SUBTITLE_ERROR,
    SUBTITLE_MISSING,
    SUBTITLE_OK,
    SUBTITLE_PAID_UNREACHABLE,
    PlayerInfo,
    download_subtitle_transcript,
)
from opinion_radar.collectors.exceptions import CollectorError
from opinion_radar.domain.models import ContentItem, ContentType, Platform, ProcessingStatus


class BilibiliConnector(Connector):
    platform = Platform.BILIBILI

    def list_updates(self, since: datetime) -> list[ContentItem]:
        """打开博主空间动态页，拦截动态流并返回 since 之后的新内容。"""
        logger.info(
            f"list_updates_start source={self.source.name or self.source.uid} "
            f"uid={self.source.uid} since={since.isoformat()}"
        )
        with BilibiliBrowser(self.browser_config) as browser:
            items = browser.list_dynamics(uid=self.source.uid, since=since)
        logger.info(f"list_updates_done uid={self.source.uid} items={len(items)}")
        return items

    def fetch_detail(self, item: ContentItem) -> ContentItem:
        """获取单条内容详情：图文动态走详情页拦截，视频走播放页字幕拦截。"""
        if item.content_type is ContentType.VIDEO:
            merged = self.fetch_video_details([item])
            detail = merged.get(item.external_id)
            if detail is None:
                raise CollectorError(f"视频字幕抓取失败：bvid={item.external_id}")
            return detail
        dynamic_id = str(item.payload.get("dynamic_id") or item.external_id)
        merged = self.fetch_details([item])
        detail = merged.get(dynamic_id)
        if detail is None:
            raise CollectorError(f"动态详情抓取失败：dynamic_id={dynamic_id}")
        return detail

    def fetch_details(self, items: list[ContentItem]) -> dict[str, ContentItem]:
        """批量抓取图文动态详情（顺带评论区 UP 主回复），返回 dynamic_id → 详情条目。

        整批共用一个浏览器会话（条间人类节奏停顿），详情字段由调用方合并回原条目。
        """
        dynamic_ids = [str(i.payload.get("dynamic_id") or i.external_id) for i in items]
        logger.info(f"fetch_details_start uid={self.source.uid} count={len(dynamic_ids)}")
        with BilibiliBrowser(self.browser_config) as browser:
            results = browser.fetch_dynamic_details(dynamic_ids, uid=self.source.uid)
        details = {
            dynamic_id: self._apply_comments(result.item, result.up_replies, result.comment_pages)
            for dynamic_id, result in results.items()
        }
        logger.info(
            f"fetch_details_done uid={self.source.uid} ok={len(details)}/{len(dynamic_ids)}"
        )
        return details

    def fetch_video_details(self, items: list[ContentItem]) -> dict[str, ContentItem]:
        """批量抓取视频字幕（顺带评论区 UP 主回复），返回 bvid → 填充了正文的条目副本。

        播放页拦截 player 接口拿到字幕轨道后，字幕 CDN 直连下载（不经浏览器）。
        合规处理：未付费的充电专属视频标记「付费不可达」，不请求字幕与评论；
        已付费的仅提取字幕文字。调用方把字段合并回原条目（merge_video_detail）。
        """
        bvids = [item.external_id for item in items]
        logger.info(f"fetch_video_details_start uid={self.source.uid} count={len(bvids)}")
        with BilibiliBrowser(self.browser_config) as browser:
            page_results = browser.fetch_video_players(bvids, uid=self.source.uid)

        results: dict[str, ContentItem] = {}
        for item in items:
            bvid = item.external_id
            page_result = page_results.get(bvid)
            if page_result is None:
                logger.warning(f"video_player_info_missing bvid={bvid}, skip")
                continue
            info = page_result.info
            detail = item.model_copy(deep=True)
            if info.is_paid and not info.paid_playable:
                # 合规红线：未付费的充电专属内容标记「付费不可达」，不请求字幕与评论
                detail.is_paid = True
                detail.body_text = None
                detail.payload["subtitle_status"] = SUBTITLE_PAID_UNREACHABLE
                detail.payload["comment_status"] = COMMENT_SKIPPED_PAID
            else:
                self._fill_subtitle(detail, info)
                self._apply_comments(detail, page_result.up_replies, page_result.comment_pages)
            results[bvid] = detail

        logger.info(
            f"fetch_video_details_done uid={self.source.uid} ok={len(results)}/{len(bvids)}"
        )
        return results

    def fetch_comments(self, items: list[ContentItem]) -> dict[str, ContentItem]:
        """纯评论回填：只采评论区 UP 主回复，返回 external_id → 带评论段落的条目副本。

        评论段落放在 payload["up_replies_section"]（不直接改 body_text），
        由调用方按幂等规则合并进已有正文（merge_comments）。
        """
        targets = [self._to_comment_target(item) for item in items]
        logger.info(f"fetch_comments_start uid={self.source.uid} count={len(targets)}")
        with BilibiliBrowser(self.browser_config) as browser:
            results = browser.fetch_comments_batch(targets, uid=self.source.uid)

        details: dict[str, ContentItem] = {}
        for item in items:
            detail = item.model_copy(deep=True)
            result = results.get(item.external_id)
            if result is None:
                detail.payload["comment_status"] = COMMENT_ERROR
            else:
                section = build_reply_section(result.up_replies)
                if section:
                    detail.payload["up_replies_section"] = section
                detail.payload["up_reply_count"] = len(result.up_replies)
                detail.payload["comment_status"] = (
                    COMMENT_OK if result.comment_pages > 0 else COMMENT_EMPTY
                )
            details[item.external_id] = detail
        logger.info(f"fetch_comments_done uid={self.source.uid} ok={len(results)}/{len(items)}")
        return details

    def _to_comment_target(self, item: ContentItem) -> CommentTarget:
        """把归档条目映射成评论回填目标页面（视频 aid 由页面拦截响应自行解析）。"""
        if item.content_type is ContentType.VIDEO:
            return CommentTarget(
                external_id=item.external_id,
                url=VIDEO_URL_TEMPLATE.format(bvid=item.external_id),
                comment_type=COMMENT_TYPE_VIDEO,
            )
        dynamic_id = str(item.payload.get("dynamic_id") or item.external_id)
        return CommentTarget(
            external_id=item.external_id,
            url=DYNAMIC_URL_TEMPLATE.format(dynamic_id=dynamic_id),
            comment_type=COMMENT_TYPE_DYNAMIC,
        )

    @staticmethod
    def _fill_subtitle(detail: ContentItem, info: PlayerInfo) -> None:
        """按字幕轨道下载转写稿填入 detail（付费不可达之外的所有情况）。"""
        if info.subtitle_url:
            try:
                transcript = download_subtitle_transcript(info.subtitle_url)
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning(f"subtitle_download_failed bvid={info.bvid} error={exc}")
                detail.payload["subtitle_status"] = SUBTITLE_ERROR
                return
            detail.is_paid = info.is_paid
            detail.body_text = transcript or None
            detail.payload["subtitle_status"] = SUBTITLE_OK if transcript else SUBTITLE_MISSING
            detail.payload["subtitle_lan"] = info.subtitle_lan
            if transcript and detail.status is ProcessingStatus.DISCOVERED:
                detail.status = ProcessingStatus.FETCHED
        else:
            detail.payload["subtitle_status"] = SUBTITLE_MISSING

    @staticmethod
    def _apply_comments(
        detail: ContentItem, up_replies: list[UpReply], comment_pages: int
    ) -> ContentItem:
        """把评论区 UP 主回复渲染成段落拼入正文，并写入评论采集状态。"""
        section = build_reply_section(up_replies)
        if section:
            body = (detail.body_text or "").strip()
            detail.body_text = f"{body}\n\n{section}" if body else section
            if detail.status is ProcessingStatus.DISCOVERED:
                detail.status = ProcessingStatus.FETCHED
        detail.payload["up_reply_count"] = len(up_replies)
        detail.payload["comment_status"] = COMMENT_OK if comment_pages > 0 else COMMENT_EMPTY
        return detail
