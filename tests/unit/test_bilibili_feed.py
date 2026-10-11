"""B 站动态流解析器单测：用录制脱敏的 feed/space 响应做对拍。

接口结构变化时，先更新 fixture 再改解析逻辑，保证映射与真实响应一致。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opinion_radar.collectors.bilibili.feed import (
    DYNAMIC_URL_TEMPLATE,
    FEED_API_MARKER,
    VIDEO_URL_TEMPLATE,
    FeedPage,
    parse_feed_page,
    raise_for_api_code,
)
from opinion_radar.collectors.exceptions import (
    CollectorResponseError,
    LoginRequiredError,
    RiskControlBlocked,
)
from opinion_radar.domain.models import ContentType

FIXTURE = Path(__file__).parent.parent / "fixtures" / "bilibili" / "feed_space.json"
SOURCE_UID = "90001"


@pytest.fixture
def feed_payload() -> dict:  # type: ignore[type-arg]
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_marker_matches_real_endpoint() -> None:
    # 拦截器靠这个 URL 片段识别动态流响应
    url = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?host_mid=90001"
    assert FEED_API_MARKER in url


def test_parse_feed_page_maps_all_supported_types(feed_payload: dict) -> None:  # type: ignore[type-arg]
    page = parse_feed_page(feed_payload, source_uid=SOURCE_UID)

    assert isinstance(page, FeedPage)
    assert page.has_more is True
    # 置顶视频 + 文字 + 图文 + opus + 转发 = 5；直播/音乐被跳过
    assert [i.content_type for i in page.items] == [
        ContentType.VIDEO,
        ContentType.MOMENT,
        ContentType.MOMENT,
        ContentType.MOMENT,
        ContentType.MOMENT,
    ]


def test_parse_video_dynamic(feed_payload: dict) -> None:  # type: ignore[type-arg]
    page = parse_feed_page(feed_payload, source_uid=SOURCE_UID)
    video = page.items[0]

    assert video.content_type is ContentType.VIDEO
    assert video.external_id == "BV1TEST0001"
    assert str(video.url) == VIDEO_URL_TEMPLATE.format(bvid="BV1TEST0001")
    assert video.title == "四季度行情怎么看"
    assert video.body_text is None  # 字幕在下一阶段播放页拦截填充
    assert video.payload["dynamic_id"] == "7890000000000000001"
    assert video.payload["is_pinned"] is True
    assert video.source_id == SOURCE_UID
    assert video.published_at is not None and video.published_at.year == 2026


def test_parse_word_dynamic(feed_payload: dict) -> None:  # type: ignore[type-arg]
    word = parse_feed_page(feed_payload, source_uid=SOURCE_UID).items[1]

    assert word.content_type is ContentType.MOMENT
    assert word.external_id == "7890000000000000002"
    assert str(word.url) == DYNAMIC_URL_TEMPLATE.format(dynamic_id="7890000000000000002")
    assert word.title == "刚出的社融数据偏弱，短期对成长板块保持谨慎。"
    assert word.body_text == "刚出的社融数据偏弱，短期对成长板块保持谨慎。"
    assert word.payload["image_urls"] == []
    assert word.payload.get("is_pinned") is None


def test_parse_draw_dynamic_normalizes_image_scheme(feed_payload: dict) -> None:  # type: ignore[type-arg]
    draw = parse_feed_page(feed_payload, source_uid=SOURCE_UID).items[2]

    assert draw.content_type is ContentType.MOMENT
    assert draw.title == "今天路演的几张关键图表"
    assert draw.body_text == "今天路演的几张关键图表"
    assert draw.payload["image_urls"] == [
        "https://i0.hdslb.com/bfs/new_dyn/pic1.jpg",
        "https://i0.hdslb.com/bfs/new_dyn/pic2.jpg",
    ]


def test_parse_opus_dynamic(feed_payload: dict) -> None:  # type: ignore[type-arg]
    opus = parse_feed_page(feed_payload, source_uid=SOURCE_UID).items[3]

    assert opus.title == "周观点：红利资产的再定价"
    assert opus.body_text == "本周讨论红利风格的估值修复空间与利率环境。"
    assert opus.payload["image_urls"] == ["https://i0.hdslb.com/bfs/new_dyn/opus1.jpg"]
    assert opus.payload["dynamic_type"] == "DYNAMIC_TYPE_ARTICLE"


def test_parse_forward_dynamic(feed_payload: dict) -> None:  # type: ignore[type-arg]
    forward = parse_feed_page(feed_payload, source_uid=SOURCE_UID).items[4]

    assert forward.content_type is ContentType.MOMENT
    assert forward.payload["orig_dynamic_id"] == "7890000000000000099"
    assert "这份宏观研报的框架值得参考" in (forward.body_text or "")
    assert "【转发原文】" in (forward.body_text or "")
    assert "某机构宏观框架长文" in (forward.body_text or "")


def test_risk_control_code_raises() -> None:
    with pytest.raises(RiskControlBlocked):
        raise_for_api_code({"code": -352, "message": "请求被拦截"})


def test_not_login_code_raises() -> None:
    with pytest.raises(LoginRequiredError):
        raise_for_api_code({"code": -101, "message": "账号未登录"})


def test_unexpected_code_raises_response_error() -> None:
    with pytest.raises(CollectorResponseError) as exc_info:
        raise_for_api_code({"code": -404, "message": "啥也没有"})
    assert exc_info.value.code == -404


def test_missing_data_raises_response_error(feed_payload: dict) -> None:  # type: ignore[type-arg]
    feed_payload["data"] = None
    with pytest.raises(CollectorResponseError):
        parse_feed_page(feed_payload, source_uid=SOURCE_UID)


def test_video_dynamic_without_bvid_raises(feed_payload: dict) -> None:  # type: ignore[type-arg]
    video_item = feed_payload["data"]["items"][0]
    video_item["modules"]["module_dynamic"]["major"]["archive"]["bvid"] = ""
    with pytest.raises(CollectorResponseError):
        parse_feed_page(feed_payload, source_uid=SOURCE_UID)
