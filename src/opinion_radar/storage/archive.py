"""本地文件归档：把抓取到的内容按「UP ID + 名称 / 时间」写入本地 JSON。

硬约束：抓取内容只落本地（data/archive/，已随 data/ 被 gitignore），
任何环节都不得上传远程；微信推送只发 LLM 观点摘要。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from loguru import logger

from opinion_radar.domain.models import ContentItem, ContentType, ProcessingStatus

DEFAULT_ARCHIVE_DIR = Path("./data/archive")

_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\s]+')

# 无标题动态的兜底标题，详情抓取后若拿到真标题则替换
_GENERIC_TITLE = "图文动态"


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
    已存在的文件直接跳过（幂等，可反复补抓）。
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
    """图文动态是否缺详情：正文缺失（或仅标题占位）、图片为空任一即需要回填。"""
    if item.content_type is not ContentType.MOMENT:
        return False
    body = (item.body_text or "").strip()
    has_body = bool(body) and body != item.title.strip()
    has_images = bool(item.payload.get("image_urls"))
    return not (has_body and has_images)


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


def overwrite_archive_item(path: Path, item: ContentItem) -> None:
    """用更新后的条目覆写归档文件（仅回填详情场景使用，普通归档仍幂等跳过）。"""
    path.write_text(
        json.dumps(item.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
