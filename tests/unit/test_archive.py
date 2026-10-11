"""本地归档存储单测：详情回填的读取、合并、覆写行为。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from opinion_radar.domain.models import ContentItem, ContentType, Platform, ProcessingStatus
from opinion_radar.storage.archive import (
    load_archive_items,
    merge_detail,
    needs_detail,
    overwrite_archive_item,
)

UID = "90001"


def _moment(
    external_id: str,
    *,
    body: str | None = None,
    images: list[str] | None = None,
    title: str = "图文动态",
) -> ContentItem:
    return ContentItem(
        platform=Platform.BILIBILI,
        source_id=UID,
        content_type=ContentType.MOMENT,
        external_id=external_id,
        title=title,
        url=f"https://t.bilibili.com/{external_id}",
        published_at=datetime(2026, 9, 1, tzinfo=UTC),
        body_text=body,
        payload={"dynamic_id": external_id, "image_urls": images or []},
    )


def test_needs_detail_only_for_moments_with_missing_content() -> None:
    assert needs_detail(_moment("1")) is True  # 无正文无图片
    assert needs_detail(_moment("2", body="有正文")) is True  # 缺图片
    assert needs_detail(_moment("3", images=["https://x/pic.jpg"])) is True  # 缺正文
    assert needs_detail(_moment("4", body="正文", images=["https://x/pic.jpg"])) is False
    # 正文只是标题占位（发现阶段回填）时仍视为缺详情
    assert needs_detail(_moment("6", body="地产逻辑", title="地产逻辑")) is True

    video = _moment("5", body="正文", images=["https://x/pic.jpg"])
    video.content_type = ContentType.VIDEO
    assert needs_detail(video) is False


def test_merge_detail_fills_missing_and_keeps_existing() -> None:
    existing = _moment("1", body="已有正文", images=[], title="图文动态")
    existing.payload["is_pinned"] = True
    detail = _moment(
        "1",
        body="详情正文",
        images=["https://x/a.jpg"],
        title="详情标题",
    )
    detail.payload["author_name"] = "示例财经博主"

    merged = merge_detail(existing, detail)

    # 已有非空字段不覆盖；通用兜底标题可被详情标题替换
    assert merged.body_text == "已有正文"
    assert merged.payload["image_urls"] == ["https://x/a.jpg"]
    assert merged.title == "详情标题"
    assert merged.payload["is_pinned"] is True
    # 详情新增的 payload 键合入
    assert merged.payload["author_name"] == "示例财经博主"


def test_merge_detail_updates_status_only_when_body_filled() -> None:
    empty = merge_detail(_moment("1"), _moment("1", body="详情正文", images=["https://x/a.jpg"]))
    assert empty.body_text == "详情正文"
    assert empty.status is ProcessingStatus.FETCHED

    images_only = merge_detail(_moment("1"), _moment("1", images=["https://x/a.jpg"]))
    assert images_only.body_text is None
    assert images_only.status is ProcessingStatus.DISCOVERED


def test_merge_detail_replaces_title_fallback_body() -> None:
    # 发现阶段正文缺失时用标题占位，详情正文更长时应替换而非保留占位
    existing = _moment("1", body="近期的市场，在担心什么？", title="近期的市场，在担心什么？")
    detail = _moment("1", body="近期的市场，在担心什么？\n两大原因：PMI 超预期与美债拍卖暴雷。")
    merged = merge_detail(existing, detail)
    assert merged.body_text.startswith("近期的市场，在担心什么？\n两大原因")


def test_load_and_overwrite_archive_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / f"{UID}-示例博主"
    target.mkdir()
    path = target / "20260901-120000_moment_1.json"
    path.write_text(
        json.dumps(_moment("1").model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )

    loaded = load_archive_items(UID, base_dir=tmp_path)
    assert [item.external_id for _, item in loaded] == ["1"]

    merged = merge_detail(loaded[0][1], _moment("1", body="详情正文"))
    overwrite_archive_item(path, merged)
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded["body_text"] == "详情正文"
    assert reloaded["status"] == "fetched"


def test_load_archive_items_skips_broken_file(tmp_path: Path) -> None:
    target = tmp_path / f"{UID}-示例博主"
    target.mkdir()
    (target / "broken.json").write_text("{not json", encoding="utf-8")
    assert load_archive_items(UID, base_dir=tmp_path) == []
