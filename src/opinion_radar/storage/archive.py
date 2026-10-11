"""本地文件归档：把抓取到的内容按「UP ID + 名称 / 时间」写入本地 JSON。

硬约束：抓取内容只落本地（data/archive/，已随 data/ 被 gitignore），
任何环节都不得上传远程；微信推送只发 LLM 观点摘要。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from loguru import logger

from opinion_radar.domain.models import ContentItem

DEFAULT_ARCHIVE_DIR = Path("./data/archive")

_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\s]+')


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
