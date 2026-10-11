"""B 站动态详情解析器单测：用录制脱敏的 detail / opus 响应做对拍。

覆盖新版图文动态的两类详情载体：
- DYNAMIC_TYPE_DRAW 但图片在 major.opus.pics（feed 阶段拿不到正文图片的根因）；
- DYNAMIC_TYPE_OPUS（标题 + 摘要 + 图片）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opinion_radar.collectors.bilibili.feed import (
    DETAIL_API_MARKERS,
    DYNAMIC_URL_TEMPLATE,
    parse_detail_item,
)
from opinion_radar.collectors.exceptions import (
    CollectorResponseError,
    RiskControlBlocked,
)
from opinion_radar.domain.models import ContentType

FIXTURES = Path(__file__).parent.parent / "fixtures" / "bilibili"
SOURCE_UID = "90001"


def _load(name: str) -> dict:  # type: ignore[type-arg]
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_detail_markers_match_real_endpoints() -> None:
    # 拦截器靠这些 URL 片段识别详情响应（opus/detail 覆盖新版图文跳转页）
    for url in (
        "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?id=789",
        "https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/detail?opus_id=789",
    ):
        assert any(marker in url for marker in DETAIL_API_MARKERS)


def test_parse_detail_draw_with_opus_payload() -> None:
    item = parse_detail_item(_load("detail_draw_opus.json"), source_uid=SOURCE_UID)

    assert item.content_type is ContentType.MOMENT
    assert item.external_id == "7890000000000000003"
    assert str(item.url) == DYNAMIC_URL_TEMPLATE.format(dynamic_id="7890000000000000003")
    # desc.text 为空时正文 / 标题从 opus.summary.text 兜底（真实详情接口的正文位置）
    assert item.title == "今天路演的几张关键图表"
    assert item.body_text == "今天路演的几张关键图表，重点看第二张的利差走势。"
    # draw 结构缺失时从 opus.pics 兜底取图，并把 http 归一化成 https
    assert item.payload["image_urls"] == [
        "https://i0.hdslb.com/bfs/new_dyn/opus_a.jpg",
        "https://i0.hdslb.com/bfs/new_dyn/opus_b.jpg",
    ]
    assert item.source_id == SOURCE_UID


def test_parse_detail_opus_type() -> None:
    item = parse_detail_item(_load("detail_opus.json"), source_uid=SOURCE_UID)

    assert item.content_type is ContentType.MOMENT
    assert item.title == "周观点：红利资产的再定价"
    assert item.body_text == "本周讨论红利风格的估值修复空间与利率环境。"
    assert item.payload["image_urls"] == ["https://i0.hdslb.com/bfs/new_dyn/opus_c.jpg"]


def test_parse_detail_summary_text_fallback() -> None:
    # feed 预览结构用 summary_text（旧字段），详情解析同样兼容
    payload = {
        "code": 0,
        "data": {
            "item": {
                "id_str": "7890000000000000009",
                "type": "DYNAMIC_TYPE_ARTICLE",
                "modules": {
                    "module_author": {"name": "示例财经博主"},
                    "module_dynamic": {
                        "major": {
                            "opus": {
                                "title": "旧字段标题",
                                "summary_text": "旧字段摘要正文",
                            }
                        }
                    },
                },
            }
        },
    }
    item = parse_detail_item(payload, source_uid=SOURCE_UID)
    assert item.body_text == "旧字段摘要正文"


def test_parse_detail_risk_control_raises() -> None:
    with pytest.raises(RiskControlBlocked):
        parse_detail_item({"code": -352, "message": "请求被拦截"}, source_uid=SOURCE_UID)


def test_parse_detail_missing_item_raises() -> None:
    with pytest.raises(CollectorResponseError):
        parse_detail_item({"code": 0, "data": {}}, source_uid=SOURCE_UID)
