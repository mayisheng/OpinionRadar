"""播放页 player 接口与字幕解析单测：用录制脱敏的 player / 字幕响应对拍。

覆盖：
- 字幕轨道选择（优先 ai-zh）、协议相对 URL 的 https 归一化；
- 充电专属视频的付费标记（未付费 → 付费不可达，不产出字幕 URL 之外的行为）；
- 字幕 CDN JSON → 转写稿的拼接（跳过空行）；
- 归档回填的判定与合并（needs_video_detail / merge_video_detail）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from opinion_radar.collectors.bilibili.feed import VIDEO_URL_TEMPLATE
from opinion_radar.collectors.bilibili.subtitle import (
    PLAYER_API_MARKERS,
    SUBTITLE_MISSING,
    SUBTITLE_OK,
    SUBTITLE_PAID_UNREACHABLE,
    parse_player_info,
    transcript_from_json,
)
from opinion_radar.collectors.exceptions import (
    CollectorResponseError,
    RiskControlBlocked,
)
from opinion_radar.domain.models import ContentItem, ContentType, Platform, ProcessingStatus

FIXTURES = Path(__file__).parent.parent / "fixtures" / "bilibili"


def _load(name: str) -> dict:  # type: ignore[type-arg]
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_player_markers_match_real_endpoints() -> None:
    # 拦截器靠这些 URL 片段识别播放页接口响应
    for url in (
        "https://api.bilibili.com/x/player/wbi/v2?aid=1&cid=2",
        "https://api.bilibili.com/x/player/v2?aid=1&cid=2",
    ):
        assert any(marker in url for marker in PLAYER_API_MARKERS)


def test_parse_player_picks_zh_track() -> None:
    info = parse_player_info(_load("player_v2.json"))

    assert info.bvid == "BV00TESTPLAYER01"
    assert info.is_paid is False
    assert info.paid_playable is False
    # 多条轨道时优先 ai-zh，协议相对 URL 归一化为 https
    assert (
        info.subtitle_url
        == "https://aisubtitle.hdslb.com/bfs/ai_subtitle/prod/zh_fixture?auth_key=111-abc-0-def"
    )
    assert info.subtitle_lan == "ai-zh"


def test_parse_player_paid_unreachable() -> None:
    # 充电专属且未付费：无字幕轨道，付费标记置位（红线：不请求字幕）
    info = parse_player_info(_load("player_v2_paid.json"))

    assert info.is_paid is True
    assert info.paid_playable is False
    assert info.subtitle_url is None


def test_parse_player_no_subtitle_track() -> None:
    payload = {"code": 0, "data": {"bvid": "BV00TESTEMPTY01", "subtitle": {"subtitles": []}}}

    info = parse_player_info(payload)
    assert info.subtitle_url is None
    assert info.is_paid is False


def test_parse_player_risk_control_raises() -> None:
    with pytest.raises(RiskControlBlocked):
        parse_player_info({"code": -352, "message": "请求被拦截"})


def test_parse_player_missing_data_raises() -> None:
    with pytest.raises(CollectorResponseError):
        parse_player_info({"code": 0, "data": {}})


def test_transcript_from_json_joins_content() -> None:
    transcript = transcript_from_json(_load("subtitle_body.json"))

    # 分句按行拼接，空 content 跳过
    assert transcript == "大家好\n今天聊一聊利率环境\n以及红利资产的估值修复"


def _video_item(bvid: str = "BV00TESTPLAYER01", **payload) -> ContentItem:
    return ContentItem(
        platform=Platform.BILIBILI,
        source_id="90001",
        content_type=ContentType.VIDEO,
        external_id=bvid,
        title="测试视频标题",
        url=VIDEO_URL_TEMPLATE.format(bvid=bvid),
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        payload=payload,
    )


def test_needs_video_detail_pending_and_terminal() -> None:
    from opinion_radar.storage.archive import needs_video_detail

    # 无字幕状态的新归档 → 待回填
    assert needs_video_detail(_video_item()) is True
    assert needs_video_detail(_video_item(subtitle_status="error")) is True
    # 终态 / 已有正文 → 不再回填
    assert needs_video_detail(_video_item(subtitle_status=SUBTITLE_MISSING)) is False
    assert needs_video_detail(_video_item(subtitle_status=SUBTITLE_PAID_UNREACHABLE)) is False
    assert (
        needs_video_detail(
            _video_item(subtitle_status=SUBTITLE_OK).model_copy(update={"body_text": "已有转写稿"})
        )
        is False
    )
    # 图文动态不走该判定
    moment = _video_item().model_copy(update={"content_type": ContentType.MOMENT})
    assert needs_video_detail(moment) is False


def test_merge_video_detail_fills_body_and_status() -> None:
    from opinion_radar.storage.archive import merge_video_detail

    existing = _video_item()
    detail = existing.model_copy(deep=True)
    detail.body_text = "大家好\n今天聊一聊利率环境"
    detail.is_paid = False
    detail.payload["subtitle_status"] = SUBTITLE_OK
    detail.payload["subtitle_lan"] = "ai-zh"
    detail.status = ProcessingStatus.FETCHED

    merged = merge_video_detail(existing, detail)
    assert merged.body_text == "大家好\n今天聊一聊利率环境"
    assert merged.payload["subtitle_status"] == SUBTITLE_OK
    assert merged.payload["subtitle_lan"] == "ai-zh"
    assert merged.is_paid is False
    assert merged.status is ProcessingStatus.FETCHED


def test_merge_video_detail_marks_paid_unreachable() -> None:
    from opinion_radar.storage.archive import merge_video_detail

    existing = _video_item(bvid="BV00TESTPAID001")
    detail = existing.model_copy(deep=True)
    detail.is_paid = True
    detail.body_text = None
    detail.payload["subtitle_status"] = SUBTITLE_PAID_UNREACHABLE

    merged = merge_video_detail(existing, detail)
    assert merged.is_paid is True
    assert merged.body_text is None
    assert merged.payload["subtitle_status"] == SUBTITLE_PAID_UNREACHABLE
    assert merged.status is ProcessingStatus.DISCOVERED


def test_merge_video_detail_keeps_existing_body() -> None:
    from opinion_radar.storage.archive import merge_video_detail

    existing = _video_item().model_copy(update={"body_text": "已有正文"})
    detail = _video_item().model_copy(
        update={"body_text": "新正文", "status": ProcessingStatus.FETCHED}
    )

    merged = merge_video_detail(existing, detail)
    assert merged.body_text == "已有正文"
