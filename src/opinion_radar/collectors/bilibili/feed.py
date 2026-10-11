"""B 站动态流与动态详情响应纯解析器。

只做一件事：把拦截到的接口 JSON（feed/space、detail、opus/detail）
映射成跨平台的 ContentItem。
不做任何 IO，浏览器驱动在 browser.py，业务编排不在本模块。
接口结构变化时，先改这里并用 tests/fixtures/bilibili/ 下的录制响应对拍。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger
from pydantic import BaseModel, HttpUrl

from opinion_radar.collectors.exceptions import (
    CollectorResponseError,
    LoginRequiredError,
    RiskControlBlocked,
)
from opinion_radar.domain.models import ContentItem, ContentType, Platform

# 拦截匹配用的 URL 片段（浏览器侧据此识别动态流响应）
FEED_API_MARKER = "polymer/web-dynamic/v1/feed/space"
# 动态详情响应（详情页 t.bilibili.com/{id} 加载；opus/detail 覆盖新版图文）
DETAIL_API_MARKERS = ("web-dynamic/v1/detail", "web-dynamic/v1/opus")

VIDEO_URL_TEMPLATE = "https://www.bilibili.com/video/{bvid}"
DYNAMIC_URL_TEMPLATE = "https://t.bilibili.com/{dynamic_id}"

# 业务码参考：https://github.com/SocialSisterYi/bilibili-API-collect
_CODE_OK = 0
_CODE_NOT_LOGIN = -101
_RISK_CODES = frozenset({-352, -412, -799})

# 不产出观点内容、直接跳过的动态类型
_SKIP_TYPES = frozenset(
    {
        "DYNAMIC_TYPE_LIVE",
        "DYNAMIC_TYPE_LIVE_RCMD",
        "DYNAMIC_TYPE_CUSTOM_RECORD",
        "DYNAMIC_TYPE_COURSES_SEASON",
        "DYNAMIC_TYPE_COURSES_BATCH",
        "DYNAMIC_TYPE_SUBSCRIPTION",
    }
)

_TITLE_MAX_LEN = 30


class FeedPage(BaseModel):
    """单页动态流的解析结果。"""

    has_more: bool
    items: list[ContentItem]


def raise_for_api_code(payload: dict[str, Any]) -> None:
    """检查动态流响应的业务码，异常状态直接抛出（由浏览器层熔断处理）。"""
    code = payload.get("code")
    if code == _CODE_OK:
        return
    message = str(payload.get("message") or payload.get("msg") or "未知错误")
    if code == _CODE_NOT_LOGIN:
        raise LoginRequiredError(f"B 站登录态缺失或已失效（code={code}）：请先执行 bilibili-login")
    if code in _RISK_CODES:
        raise RiskControlBlocked(
            f"B 站风控拦截（code={code}，message={message}）：本轮熔断，不重试"
        )
    raise CollectorResponseError(
        f"动态流返回异常业务码（code={code}，message={message}）", code=code
    )


def parse_feed_page(payload: dict[str, Any], *, source_uid: str) -> FeedPage:
    """解析一整页动态流响应。

    无法识别 / 明确跳过的动态类型记 debug 日志后忽略，不阻断整页。
    """
    raise_for_api_code(payload)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise CollectorResponseError("动态流响应缺少 data 对象（接口结构可能已变化）")

    items: list[ContentItem] = []
    for raw_item in data.get("items") or []:
        if not isinstance(raw_item, dict):
            continue
        item = _parse_item(raw_item, source_uid=source_uid)
        if item is not None:
            items.append(item)

    has_more = bool(data.get("has_more", False))
    return FeedPage(has_more=has_more, items=items)


def parse_detail_item(payload: dict[str, Any], *, source_uid: str) -> ContentItem:
    """解析动态详情响应（detail / opus/detail），结构与 feed 的单个 item 相同。

    与 parse_feed_page 复用 raise_for_api_code 和 _parse_item，接口改版时一处维护。
    """
    raise_for_api_code(payload)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise CollectorResponseError("动态详情响应缺少 data 对象（接口结构可能已变化）")
    raw_item = data.get("item")
    if not isinstance(raw_item, dict):
        raise CollectorResponseError("动态详情响应缺少 data.item（接口结构可能已变化）")
    item = _parse_item(raw_item, source_uid=source_uid)
    if item is None:
        raise CollectorResponseError(f"动态详情返回了不支持的类型（type={raw_item.get('type')}）")
    return item


def _parse_item(raw: dict[str, Any], *, source_uid: str) -> ContentItem | None:
    dyn_type = str(raw.get("type") or "")
    dynamic_id = str(raw.get("id_str") or "")
    modules = raw.get("modules")
    modules = modules if isinstance(modules, dict) else {}
    author = modules.get("module_author")
    author = author if isinstance(author, dict) else {}
    dynamic = modules.get("module_dynamic")
    dynamic = dynamic if isinstance(dynamic, dict) else {}
    desc = dynamic.get("desc")
    desc = desc if isinstance(desc, dict) else {}
    text = str(desc.get("text") or "").strip()
    major = dynamic.get("major")
    major = major if isinstance(major, dict) else {}
    published_at = _parse_pub_ts(author.get("pub_ts"))

    if dyn_type in _SKIP_TYPES:
        logger.debug(f"skip dynamic {dynamic_id} type={dyn_type}")
        return None

    if dyn_type == "DYNAMIC_TYPE_AV":
        item = _parse_video(dynamic_id, major, published_at, source_uid)
    elif dyn_type == "DYNAMIC_TYPE_DRAW":
        item = _parse_draw(dynamic_id, text, major, published_at, source_uid)
    elif dyn_type in ("DYNAMIC_TYPE_ARTICLE", "DYNAMIC_TYPE_OPUS"):
        item = _parse_article(dynamic_id, major, published_at, source_uid)
    elif dyn_type == "DYNAMIC_TYPE_WORD":
        item = _build_moment(dynamic_id, text, [], text, published_at, source_uid, dyn_type)
    elif dyn_type == "DYNAMIC_TYPE_FORWARD":
        item = _parse_forward(dynamic_id, text, raw, published_at, source_uid)
    else:
        logger.debug(f"skip dynamic {dynamic_id} unknown type={dyn_type}")
        return None

    author_name = str(author.get("name") or "").strip()
    if author_name:
        item.payload["author_name"] = author_name
    tag = modules.get("module_tag")
    if isinstance(tag, dict) and "顶" in str(tag.get("text") or ""):
        item.payload["is_pinned"] = True
    return item


def _parse_video(
    dynamic_id: str,
    major: dict[str, Any],
    published_at: datetime | None,
    source_uid: str,
) -> ContentItem:
    archive = major.get("archive")
    archive = archive if isinstance(archive, dict) else {}
    bvid = str(archive.get("bvid") or "").strip()
    if not bvid:
        raise CollectorResponseError(f"视频动态 {dynamic_id} 缺少 bvid（接口结构可能已变化）")
    return ContentItem(
        platform=Platform.BILIBILI,
        source_id=source_uid,
        content_type=ContentType.VIDEO,
        external_id=bvid,
        title=str(archive.get("title") or bvid),
        url=HttpUrl(VIDEO_URL_TEMPLATE.format(bvid=bvid)),
        published_at=published_at,
        # 正文（字幕）在播放页拦截阶段填充
        payload={
            "dynamic_id": dynamic_id,
            "dynamic_type": "DYNAMIC_TYPE_AV",
            "desc": str(archive.get("desc") or ""),
        },
    )


def _parse_draw(
    dynamic_id: str,
    text: str,
    major: dict[str, Any],
    published_at: datetime | None,
    source_uid: str,
) -> ContentItem:
    draw = major.get("draw")
    draw = draw if isinstance(draw, dict) else {}
    images = [
        str(pic.get("src") or "").replace("http://", "https://")
        for pic in draw.get("items") or []
        if isinstance(pic, dict) and pic.get("src")
    ]
    # 新版图文动态的 draw 结构迁移到 opus（pics[].url + summary.text），
    # feed 预览与详情响应均可能出现，desc.text 为空时正文从 opus 兜底
    title, opus_text, opus_images = _opus_parts(major)
    if not images:
        images = opus_images
    return _build_moment(
        dynamic_id,
        title or text,
        images,
        text or opus_text,
        published_at,
        source_uid,
        "DYNAMIC_TYPE_DRAW",
    )


def _opus_parts(major: dict[str, Any]) -> tuple[str, str, list[str]]:
    """从 major.opus 提取（标题, 正文, 图片列表），兼容新旧字段。

    详情接口的正文在 summary.text，feed 预览在 summary_text，两者取先非空者。
    """
    opus = major.get("opus")
    opus = opus if isinstance(opus, dict) else {}
    title = str(opus.get("title") or "").strip()
    summary = opus.get("summary")
    text = str(summary.get("text") or "").strip() if isinstance(summary, dict) else ""
    text = text or str(opus.get("summary_text") or "").strip()
    images = [
        str(pic.get("url") or "").replace("http://", "https://")
        for pic in opus.get("pics") or []
        if isinstance(pic, dict) and pic.get("url")
    ]
    return title, text, images


def _parse_article(
    dynamic_id: str,
    major: dict[str, Any],
    published_at: datetime | None,
    source_uid: str,
) -> ContentItem:
    title, text, images = _opus_parts(major)
    return _build_moment(
        dynamic_id,
        title or text,
        images,
        text or title,
        published_at,
        source_uid,
        "DYNAMIC_TYPE_ARTICLE",
    )


def _parse_forward(
    dynamic_id: str,
    text: str,
    raw: dict[str, Any],
    published_at: datetime | None,
    source_uid: str,
) -> ContentItem:
    orig = raw.get("orig")
    orig = orig if isinstance(orig, dict) else {}
    orig_id = str(orig.get("id_str") or "")
    orig_type = str(orig.get("type") or "")
    orig_text = _extract_orig_text(orig)

    # 转发语是博主本人观点，原文摘要拼在后面提供上下文（正文归属仍是这条动态）
    body = text
    if orig_text:
        body = f"{body}\n【转发原文】{orig_text}".strip()
    return _build_moment(
        dynamic_id,
        text,
        [],
        body,
        published_at,
        source_uid,
        "DYNAMIC_TYPE_FORWARD",
        extra_payload={"orig_dynamic_id": orig_id, "orig_type": orig_type},
    )


def _extract_orig_text(orig: dict[str, Any]) -> str:
    """从被转发动态里提取一段文本摘要（原视频的观点由视频链路单独分析）。"""
    modules = orig.get("modules")
    modules = modules if isinstance(modules, dict) else {}
    dynamic = modules.get("module_dynamic")
    dynamic = dynamic if isinstance(dynamic, dict) else {}
    desc = dynamic.get("desc")
    desc = desc if isinstance(desc, dict) else {}
    if desc.get("text"):
        return str(desc["text"])
    major = dynamic.get("major")
    major = major if isinstance(major, dict) else {}
    archive = major.get("archive")
    if isinstance(archive, dict) and archive.get("title"):
        return str(archive["title"])
    opus = major.get("opus")
    if isinstance(opus, dict):
        return str(opus.get("title") or opus.get("summary_text") or "")
    return ""


def _build_moment(
    dynamic_id: str,
    title_source: str,
    images: list[str],
    body_text: str,
    published_at: datetime | None,
    source_uid: str,
    dyn_type: str,
    extra_payload: dict[str, Any] | None = None,
) -> ContentItem:
    payload: dict[str, Any] = {
        "dynamic_id": dynamic_id,
        "dynamic_type": dyn_type,
        "image_urls": images,
    }
    if extra_payload:
        payload.update(extra_payload)
    return ContentItem(
        platform=Platform.BILIBILI,
        source_id=source_uid,
        content_type=ContentType.MOMENT,
        external_id=dynamic_id,
        title=_make_title(title_source),
        url=HttpUrl(DYNAMIC_URL_TEMPLATE.format(dynamic_id=dynamic_id)),
        published_at=published_at,
        body_text=body_text or None,  # 图片 OCR 在迭代 002 拼入
        payload=payload,
    )


def _make_title(text: str) -> str:
    """纯文字动态没有标题：取首行截断，空内容给兜底名。"""
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first_line:
        return "图文动态"
    if len(first_line) <= _TITLE_MAX_LEN:
        return first_line
    return first_line[:_TITLE_MAX_LEN] + "…"


def _parse_pub_ts(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None
