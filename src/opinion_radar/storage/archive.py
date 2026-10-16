"""本地文件归档：把抓取到的内容按「UP ID + 名称 / 时间」写入本地 JSON。

硬约束：抓取内容只落本地（data/archive/，已随 data/ 被 gitignore），
任何环节都不得上传远程；微信推送只发 LLM 观点摘要。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from loguru import logger

from opinion_radar.collectors.bilibili.comment import COMMENT_ERROR, COMMENT_SECTION_TITLE
from opinion_radar.domain.models import ContentItem, ContentType, ProcessingStatus
from opinion_radar.utils.http import create_client

DEFAULT_ARCHIVE_DIR = Path("./data/archive")

_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\s]+')

# 无标题动态的兜底标题，详情抓取后若拿到真标题则替换
_GENERIC_TITLE = "图文动态"

# 图片落盘子目录：data/archive/{uid}-{名称}/images/{URL 文件名}
_IMAGES_DIRNAME = "images"


def _image_filename(url: str) -> str | None:
    """取图片 URL 路径最后一段作为本地文件名（B 站 bfs URL 为唯一 hash + 扩展名）。"""
    name = urlsplit(url).path.rsplit("/", 1)[-1]
    return name or None


def download_item_images(item: ContentItem, *, archive_dir: Path) -> tuple[int, int, int]:
    """把条目 payload 中 image_urls 指向的图片下载到归档目录 images/ 子目录。

    图片 CDN 属静态资源域，直接 httpx 下载，不经浏览器、不消耗主站风控基线；
    内容只落本地（data/archive/ 已 gitignore），不上传远程。
    文件已存在则跳过（幂等）；先写 .part 临时文件再原子改名，
    避免网络中断留下半截文件被幂等逻辑误判为已下载。
    返回（成功, 跳过, 失败）；单张失败只告警，不中断其余下载。
    """
    urls = [u for u in item.payload.get("image_urls") or [] if isinstance(u, str)]
    if not urls:
        return 0, 0, 0
    images_dir = archive_dir / _IMAGES_DIRNAME
    images_dir.mkdir(parents=True, exist_ok=True)

    ok = skipped = failed = 0
    with create_client(headers={"Referer": "https://www.bilibili.com/"}) as client:
        for url in urls:
            name = _image_filename(url)
            if name is None:
                failed += 1
                logger.warning(f"image_download_failed url={url} reason=no filename in url")
                continue
            final = images_dir / name
            if final.exists():
                skipped += 1
                continue
            tmp = images_dir / f"{name}.part"
            try:
                resp = client.get(url)
                resp.raise_for_status()
                tmp.write_bytes(resp.content)
                tmp.replace(final)
                ok += 1
            except (httpx.HTTPError, OSError) as exc:
                failed += 1
                tmp.unlink(missing_ok=True)
                logger.warning(f"image_download_failed url={url} error={exc}")
    logger.info(f"item_images_done id={item.external_id} ok={ok} skipped={skipped} failed={failed}")
    return ok, skipped, failed


def _safe_dir_name(uid: str, author_name: str | None) -> str:
    name = _ILLEGAL_FILENAME_CHARS.sub("_", (author_name or "").strip()).strip("_")
    return f"{uid}-{name}" if name else uid


def archive_items(
    items: list[ContentItem],
    *,
    uid: str,
    author_name: str | None = None,
    base_dir: Path = DEFAULT_ARCHIVE_DIR,
) -> tuple[list[Path], int]:
    """把内容逐条写成 JSON 文件，返回（新写入的路径列表, 跳过的已存在条数）。

    目录结构：data/archive/{uid}-{名称}/{YYYYMMDD-HHMMSS}_{类型}_{external_id}.json
    已存在的文件直接跳过（幂等，可反复补抓）；条目带 image_urls 时图片落盘到 images/。
    """
    author = author_name or next(
        (str(i.payload["author_name"]) for i in items if i.payload.get("author_name")),
        None,
    )
    target_dir = base_dir / _safe_dir_name(uid, author)
    target_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    skipped = 0
    for item in items:
        ts = (
            item.published_at.astimezone().strftime("%Y%m%d-%H%M%S")
            if item.published_at
            else "unknown-time"
        )
        filename = f"{ts}_{item.content_type.value}_{item.external_id}.json"
        path = target_dir / filename
        if path.exists():
            skipped += 1
            continue
        path.write_text(
            json.dumps(item.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(path)
        download_item_images(item, archive_dir=target_dir)

    logger.info(f"archive_done dir={target_dir} new={len(written)} skipped={skipped}")
    return written, skipped


def load_archive_items(
    uid: str, *, base_dir: Path = DEFAULT_ARCHIVE_DIR
) -> list[tuple[Path, ContentItem]]:
    """读取某博主归档目录下的全部 JSON，返回（路径, 条目）；解析失败的文件跳过并告警。"""
    items: list[tuple[Path, ContentItem]] = []
    for path in sorted(base_dir.glob(f"{uid}-*/*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items.append((path, ContentItem.model_validate(data)))
        except (OSError, ValueError) as exc:
            logger.warning(f"archive_read_failed path={path} error={exc}")
    return items


def needs_detail(item: ContentItem) -> bool:
    """图文动态是否缺详情：正文缺失（或仅标题占位），或图片类动态缺图片列表。

    纯文字 / 转发 / 专栏动态本身无图，有正文即完整，不再触发详情抓取
    （否则队列里全是永远补不齐「图」的条目）；
    图片类（DRAW）有正文却无图说明详情从未抓取——图片列表只能从详情接口获得，
    需要回填后再由 download_item_images 落盘。
    """
    if item.content_type is not ContentType.MOMENT:
        return False
    body = (item.body_text or "").strip()
    title = (item.title or "").strip()
    # 正文为占位（空 / 等于默认标题）才视为缺正文；WORD 动态的 title 就是正文，
    # body==title 属正常，不误判
    has_body = bool(body) and not (title == _GENERIC_TITLE and body == title)
    if not has_body:
        return True
    is_draw = item.payload.get("dynamic_type") == "DYNAMIC_TYPE_DRAW"
    if is_draw and not item.payload.get("image_urls"):
        # 图片类动态缺图：仅在未抓过详情时回填；已抓取仍无图说明该动态本身无图
        return item.status is not ProcessingStatus.FETCHED
    return False


def merge_detail(existing: ContentItem, detail: ContentItem) -> ContentItem:
    """把详情条目的正文 / 图片 / 标题合并进已有条目，已有非空字段不覆盖。

    例外：已有正文若只是标题回填（发现阶段 body 缺失时用标题占位），
    详情拿到更完整正文时允许替换。
    """
    merged = existing.model_copy(deep=True)
    existing_body = (merged.body_text or "").strip()
    detail_body = (detail.body_text or "").strip()
    if detail_body and (not existing_body or existing_body == merged.title.strip()):
        merged.body_text = detail.body_text
    merged_payload = dict(merged.payload)
    detail_payload = dict(detail.payload)
    if not merged_payload.get("image_urls") and detail_payload.get("image_urls"):
        merged_payload["image_urls"] = detail_payload["image_urls"]
    if merged.title == _GENERIC_TITLE and detail.title != _GENERIC_TITLE:
        merged.title = detail.title
    for key, value in detail_payload.items():
        merged_payload.setdefault(key, value)
    merged.payload = merged_payload
    if (merged.body_text or "").strip() and merged.status is ProcessingStatus.DISCOVERED:
        merged.status = ProcessingStatus.FETCHED
    return merged


# 视频字幕的终态标记：这些状态下不再回填（无字幕轨道 / 未付费的充电专属，红线禁止重试）
_VIDEO_SUBTITLE_TERMINAL = ("missing", "paid_unreachable")


def needs_video_detail(item: ContentItem) -> bool:
    """视频是否缺字幕正文：无正文且字幕状态非终态（missing / paid_unreachable）。"""
    if item.content_type is not ContentType.VIDEO:
        return False
    if (item.body_text or "").strip():
        return False
    return item.payload.get("subtitle_status") not in _VIDEO_SUBTITLE_TERMINAL


def merge_video_detail(existing: ContentItem, detail: ContentItem) -> ContentItem:
    """把字幕详情条目的正文 / 付费标记 / 字幕状态合并进已有视频条目。

    已有正文字段不覆盖；字幕状态以详情为准（含「付费不可达」等终态标记）。
    """
    merged = existing.model_copy(deep=True)
    if not (merged.body_text or "").strip() and (detail.body_text or "").strip():
        merged.body_text = detail.body_text
    if detail.is_paid:
        merged.is_paid = True
    merged_payload = dict(merged.payload)
    for key in ("subtitle_status", "subtitle_lan", "comment_status", "up_reply_count"):
        value = detail.payload.get(key)
        if value is not None:
            merged_payload[key] = value
    merged.payload = merged_payload
    if (merged.body_text or "").strip() and merged.status is ProcessingStatus.DISCOVERED:
        merged.status = ProcessingStatus.FETCHED
    return merged


def needs_comments(item: ContentItem) -> bool:
    """条目是否缺评论区 UP 主回复采集：无评论状态或上轮失败（error 可重试）。

    终态（ok / empty / skipped_paid）不再重试；未付费的充电专属视频
    （老归档无 comment_status，按字幕终态识别）红线禁止采集，直接跳过。
    """
    if item.content_type not in (ContentType.VIDEO, ContentType.MOMENT):
        return False
    if item.payload.get("subtitle_status") == "paid_unreachable":
        return False
    status = item.payload.get("comment_status")
    return status is None or status == COMMENT_ERROR


def merge_comments(existing: ContentItem, detail: ContentItem) -> ContentItem:
    """把评论回填条目的「评论区 UP 主回复」段落与采集状态合并进已有条目。

    幂等：正文已含段落标题（COMMENT_SECTION_TITLE）时不重复拼接；
    评论状态以本次回填为准（含 error 标记，下轮可据此重试）。
    """
    merged = existing.model_copy(deep=True)
    section = str(detail.payload.get("up_replies_section") or "").strip()
    body = (merged.body_text or "").strip()
    if section and COMMENT_SECTION_TITLE not in body:
        merged.body_text = f"{body}\n\n{section}" if body else section
    merged_payload = dict(merged.payload)
    for key in ("comment_status", "up_reply_count"):
        value = detail.payload.get(key)
        if value is not None:
            merged_payload[key] = value
    merged.payload = merged_payload
    if (merged.body_text or "").strip() and merged.status is ProcessingStatus.DISCOVERED:
        merged.status = ProcessingStatus.FETCHED
    return merged


def overwrite_archive_item(path: Path, item: ContentItem) -> None:
    """用更新后的条目覆写归档文件（仅回填详情场景使用，普通归档仍幂等跳过）。"""
    path.write_text(
        json.dumps(item.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
