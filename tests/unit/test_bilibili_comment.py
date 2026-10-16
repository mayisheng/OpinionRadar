"""评论区 UP 主回复解析单测：用录制脱敏的主楼 / 楼中楼响应对拍。

覆盖：
- 主楼解析：UP 主自评（含置顶）过滤、up_reply 标记楼提取、分页游标、oid 提取；
- 楼中楼解析：UP 主回复过滤、page 分页判定；
- 评论段落渲染（含被回复上下文、空列表不产出）；
- 归档回填的判定与合并（needs_comments / merge_comments 幂等）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opinion_radar.collectors.bilibili.comment import (
    COMMENT_EMPTY,
    COMMENT_ERROR,
    COMMENT_MAIN_API_MARKER,
    COMMENT_OK,
    COMMENT_SECTION_TITLE,
    COMMENT_SKIPPED_PAID,
    COMMENT_TYPE_VIDEO,
    UpReply,
    build_reply_section,
    build_sub_reply_url,
    parse_comment_main_page,
    parse_sub_replies,
)
from opinion_radar.collectors.exceptions import RiskControlBlocked
from opinion_radar.domain.models import ContentItem, ContentType, Platform, ProcessingStatus
from opinion_radar.storage.archive import merge_comments, needs_comments

FIXTURES = Path(__file__).parent.parent / "fixtures" / "bilibili"
UP_MID = "9999999999"


def _load(name: str) -> dict:  # type: ignore[type-arg]
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _make_item(**overrides: object) -> ContentItem:
    data: dict[str, object] = {
        "platform": Platform.BILIBILI,
        "source_id": UP_MID,
        "content_type": ContentType.VIDEO,
        "external_id": "BV00TESTCOMMENT",
        "title": "测试视频",
        "url": "https://www.bilibili.com/video/BV00TESTCOMMENT",
        "payload": {},
    }
    data.update(overrides)
    return ContentItem.model_validate(data)


def test_comment_marker_matches_real_endpoint() -> None:
    url = (
        "https://api.bilibili.com/x/v2/reply/wbi/main?oid=11111111111&type=1&mode=3"
        "&pagination_str=%7B%22offset%22%3A%22%22%7D"
    )
    assert COMMENT_MAIN_API_MARKER in url


def test_parse_main_page_filters_up_replies() -> None:
    page = parse_comment_main_page(_load("comment_main.json"), up_mid=UP_MID)

    # UP 主自评：主楼 1 条 + 置顶 1 条；粉丝评论不入选
    assert [r.rpid for r in page.up_replies] == ["900000000004", "900000000003"]
    assert all(r.root_rpid is None for r in page.up_replies)
    assert page.up_replies[0].message == "置顶说明：周末加更一期。"
    assert page.up_replies[1].like_count == 88

    # up_reply 标记楼：只有粉丝乙那栋（rcount=18）
    assert [t.rpid for t in page.flagged] == ["900000000002"]
    assert page.flagged[0].message == "这个指标在哪里能查到？"

    assert page.oid == "11111111111"
    assert page.is_end is False


def test_parse_main_page_ignores_fan_only_page() -> None:
    payload = _load("comment_main.json")
    page = parse_comment_main_page(payload, up_mid="1234567890")
    assert page.up_replies == []
    # 标记楼与 up_mid 无关，仍要展开核对楼中楼
    assert len(page.flagged) == 1


def test_parse_sub_replies_filters_up_and_paginates() -> None:
    replies, has_more = parse_sub_replies(
        _load("comment_sub_reply.json"),
        up_mid=UP_MID,
        root_rpid="900000000002",
        root_message="这个指标在哪里能查到？",
    )

    assert len(replies) == 1
    reply = replies[0]
    assert reply.rpid == "900000000101"
    assert reply.message == "在官网的月度数据里，回复一下示意。"
    assert reply.root_rpid == "900000000002"
    assert reply.root_message == "这个指标在哪里能查到？"
    assert reply.like_count == 46
    # count=3, num=1, size=20 → 无下一页
    assert has_more is False


def test_parse_sub_replies_has_more_when_count_exceeds_page() -> None:
    payload = _load("comment_sub_reply.json")
    payload["data"]["page"]["count"] = 40
    _, has_more = parse_sub_replies(
        payload, up_mid=UP_MID, root_rpid="900000000002", root_message="x"
    )
    assert has_more is True


def test_parse_main_page_risk_code_raises() -> None:
    with pytest.raises(RiskControlBlocked):
        parse_comment_main_page({"code": -352, "message": "风控"}, up_mid=UP_MID)


def test_build_sub_reply_url() -> None:
    url = build_sub_reply_url("11111111111", COMMENT_TYPE_VIDEO, "900000000002", pn=2)
    assert url.startswith("https://api.bilibili.com/x/v2/reply/reply?")
    assert "oid=11111111111" in url
    assert "type=1" in url
    assert "root=900000000002" in url
    assert "pn=2" in url


def test_build_reply_section_with_context() -> None:
    replies = [
        UpReply(rpid="1", message="主楼自评内容"),
        UpReply(
            rpid="2",
            message="UP 的回复",
            root_rpid="100",
            root_message="粉丝的提问\n换行会被压平",
        ),
    ]
    section = build_reply_section(replies)

    assert section is not None
    assert section.startswith(f"## {COMMENT_SECTION_TITLE}")
    assert "主楼自评内容" in section
    assert "> 原评论：粉丝的提问 换行会被压平" in section
    assert "UP 的回复" in section


def test_build_reply_section_empty_returns_none() -> None:
    assert build_reply_section([]) is None


# ---- 归档回填：needs_comments / merge_comments ----


def test_needs_comments_status_matrix() -> None:
    assert needs_comments(_make_item()) is True  # 从未采过
    assert needs_comments(_make_item(payload={"comment_status": COMMENT_ERROR})) is True
    assert needs_comments(_make_item(payload={"comment_status": COMMENT_OK})) is False
    assert needs_comments(_make_item(payload={"comment_status": COMMENT_EMPTY})) is False
    assert needs_comments(_make_item(payload={"comment_status": COMMENT_SKIPPED_PAID})) is False
    # 老归档：未付费充电专属按字幕终态识别，红线禁止采集
    assert needs_comments(_make_item(payload={"subtitle_status": "paid_unreachable"})) is False
    # 非视频 / 图文类型不采
    column = _make_item(content_type=ContentType.COLUMN)
    assert needs_comments(column) is False


def test_merge_comments_appends_section_once() -> None:
    existing = _make_item(body_text="已有的字幕正文", status=ProcessingStatus.FETCHED)
    detail = _make_item(
        payload={
            "up_replies_section": f"## {COMMENT_SECTION_TITLE}\n\nUP 的回复",
            "comment_status": COMMENT_OK,
            "up_reply_count": 1,
        }
    )

    merged = merge_comments(existing, detail)
    assert merged.body_text == f"已有的字幕正文\n\n## {COMMENT_SECTION_TITLE}\n\nUP 的回复"
    assert merged.payload["comment_status"] == COMMENT_OK
    assert merged.payload["up_reply_count"] == 1

    # 幂等：再次合并不重复拼接
    merged_twice = merge_comments(merged, detail)
    assert merged_twice.body_text == merged.body_text


def test_merge_comments_error_status_carried() -> None:
    existing = _make_item(body_text="正文")
    detail = _make_item(payload={"comment_status": COMMENT_ERROR})
    merged = merge_comments(existing, detail)
    assert merged.body_text == "正文"
    assert merged.payload["comment_status"] == COMMENT_ERROR
    # error 状态下轮仍可重试
    assert needs_comments(merged) is True
