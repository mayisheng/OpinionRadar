"""本地归档存储单测：详情回填的读取、合并、覆写行为与图片落盘。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from opinion_radar.domain.models import ContentItem, ContentType, Platform, ProcessingStatus
from opinion_radar.storage import archive as archive_module
from opinion_radar.storage.archive import (
    download_item_images,
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
    dynamic_type: str | None = None,
) -> ContentItem:
    payload: dict[str, Any] = {"dynamic_id": external_id, "image_urls": images or []}
    if dynamic_type:
        payload["dynamic_type"] = dynamic_type
    return ContentItem(
        platform=Platform.BILIBILI,
        source_id=UID,
        content_type=ContentType.MOMENT,
        external_id=external_id,
        title=title,
        url=f"https://t.bilibili.com/{external_id}",
        published_at=datetime(2026, 9, 1, tzinfo=UTC),
        body_text=body,
        payload=payload,
    )


def test_needs_detail_only_for_moments_with_missing_content() -> None:
    assert needs_detail(_moment("1")) is True  # 无正文无图片
    draw = _moment("2", body="有正文", dynamic_type="DYNAMIC_TYPE_DRAW")
    assert needs_detail(draw) is True  # 图片类缺图
    assert needs_detail(_moment("3", images=["https://x/pic.jpg"])) is True  # 缺正文
    assert needs_detail(_moment("4", body="正文", images=["https://x/pic.jpg"])) is False
    # 纯文字 / 转发动态本就无图，有正文即完整
    assert needs_detail(_moment("7", body="有正文")) is False
    # 正文只是默认标题占位（发现阶段回填）时仍视为缺详情
    assert needs_detail(_moment("6", body="图文动态", title="图文动态")) is True
    # WORD 动态 title 即正文，body==title 属正常不视为占位
    assert needs_detail(_moment("8", body="地产逻辑", title="地产逻辑")) is False

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


def _mock_client(monkeypatch, handler) -> list[str]:
    """用 httpx.MockTransport 替换归档模块的 create_client，记录请求过的 URL。"""
    requested: list[str] = []

    def tracking_handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return handler(request)

    # transport 可跨 client 复用；client 每次新建（源码里是 with 上下文，退出即 close）
    transport = httpx.MockTransport(tracking_handler)
    monkeypatch.setattr(
        archive_module, "create_client", lambda **_: httpx.Client(transport=transport)
    )
    return requested


def test_download_item_images_writes_files_and_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, content=b"img-" + name.encode())

    requested = _mock_client(monkeypatch, handler)
    item = _moment("1", images=["https://i0.hdslb.com/bfs/a.png", "https://i0.hdslb.com/bfs/b.jpg"])

    ok, skipped, failed = download_item_images(item, archive_dir=tmp_path)
    assert (ok, skipped, failed) == (2, 0, 0)
    assert (tmp_path / "images" / "a.png").read_bytes() == b"img-a.png"
    assert (tmp_path / "images" / "b.jpg").read_bytes() == b"img-b.jpg"
    assert len(requested) == 2

    # 重跑幂等：已存在文件不再发请求
    ok2, skipped2, failed2 = download_item_images(item, archive_dir=tmp_path)
    assert (ok2, skipped2, failed2) == (0, 2, 0)
    assert len(requested) == 2


def test_download_item_images_continues_on_single_failure(tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "bad" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, content=b"x")

    _mock_client(monkeypatch, handler)
    item = _moment("1", images=["https://x/bad.png", "https://x/good.png"])

    ok, skipped, failed = download_item_images(item, archive_dir=tmp_path)
    assert (ok, skipped, failed) == (1, 0, 1)
    assert (tmp_path / "images" / "good.png").exists()
    # 失败与临时文件均不留残留
    assert not (tmp_path / "images" / "bad.png").exists()
    assert list(tmp_path.glob("images/*.part")) == []


def test_download_item_images_skips_when_no_urls(tmp_path: Path) -> None:
    ok, skipped, failed = download_item_images(_moment("1"), archive_dir=tmp_path)
    assert (ok, skipped, failed) == (0, 0, 0)
    assert not (tmp_path / "images").exists()


def test_archive_items_downloads_images_on_write(tmp_path: Path, monkeypatch) -> None:
    _mock_client(monkeypatch, lambda request: httpx.Response(200, content=b"img"))
    item = _moment("1", body="正文", images=["https://x/pic.png"])

    written, skipped = archive_module.archive_items([item], uid=UID, base_dir=tmp_path)

    assert len(written) == 1 and skipped == 0
    assert (written[0].parent / "images" / "pic.png").read_bytes() == b"img"
