"""播放页 player 接口与字幕 JSON 纯解析器（字段位置以 api-probe 实测为准）。

真实结构（2026-10 探测，两条样本对拍）：
- 播放页加载时拦截 api.bilibili.com/x/player/wbi/v2 响应；
- data.subtitle.subtitles[] 为字幕轨道列表（ai-zh / ai-en / ...），
  subtitle_url 是带时效 auth_key 的预签名 CDN 链接（协议相对）；
- 字幕 CDN JSON 的 body[].content 为分句文本，from/to 为秒级时间轴；
- data.is_upower_exclusive 标记充电专属视频，is_upower_play 标记当前账号已付费可播。

合规红线：未付费的充电专属视频直接标记「付费不可达」，不请求字幕；
已付费的仅提取字幕文字，不保存任何视频文件。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from opinion_radar.collectors.bilibili.feed import raise_for_api_code
from opinion_radar.collectors.exceptions import CollectorResponseError
from opinion_radar.utils.http import create_client

# 拦截匹配用的 URL 片段（wbi/v2 为当前版本，v2 为兼容旧路径）
PLAYER_API_MARKERS = ("api.bilibili.com/x/player/wbi/v2", "api.bilibili.com/x/player/v2")

# 字幕回填状态（写入 payload["subtitle_status"]，归档回填据此判断是否还需重试）
SUBTITLE_OK = "ok"
SUBTITLE_MISSING = "missing"  # 视频没有可用字幕轨道，无需重试
SUBTITLE_PAID_UNREACHABLE = "paid_unreachable"  # 未付费的充电专属，红线禁止重试
SUBTITLE_ERROR = "error"  # 下载失败等临时错误，下轮可重试

# 优先中文 AI 字幕，其次任意中文轨道，最后取第一条可用轨道
_PREFERRED_LANS = ("ai-zh", "zh-Hans", "zh-CN")


class PlayerInfo(BaseModel):
    """播放页 player 响应中与字幕采集相关的字段。"""

    bvid: str
    is_paid: bool  # 充电专属视频
    paid_playable: bool  # 当前登录账号已付费可播
    subtitle_url: str | None = None  # https 前缀的预签名 CDN 链接
    subtitle_lan: str | None = None


def parse_player_info(payload: dict[str, Any]) -> PlayerInfo:
    """解析 player/wbi/v2 响应；业务码 / 结构异常按风控约定抛出。"""
    raise_for_api_code(payload, api="播放页接口")
    data = payload.get("data")
    if not isinstance(data, dict) or not str(data.get("bvid") or "").strip():
        raise CollectorResponseError("播放页接口响应缺少 data.bvid（接口结构可能已变化）")
    subtitle_url, subtitle_lan = _pick_subtitle_track(data.get("subtitle"))
    return PlayerInfo(
        bvid=str(data.get("bvid") or ""),
        is_paid=bool(data.get("is_upower_exclusive")),
        paid_playable=bool(data.get("is_upower_play")),
        subtitle_url=subtitle_url,
        subtitle_lan=subtitle_lan,
    )


def _pick_subtitle_track(subtitle: Any) -> tuple[str | None, str | None]:
    """从 subtitle.subtitles 里挑一条可用轨道，返回（https 链接, 语言）。"""
    if not isinstance(subtitle, dict):
        return None, None
    tracks = [t for t in subtitle.get("subtitles") or [] if isinstance(t, dict)]
    tracks = [t for t in tracks if t.get("subtitle_url")]
    if not tracks:
        return None, None
    track = next(
        (t for lan in _PREFERRED_LANS for t in tracks if str(t.get("lan") or "") == lan),
        tracks[0],
    )
    url = str(track["subtitle_url"])
    if url.startswith("//"):
        url = f"https:{url}"
    return url, str(track.get("lan") or "") or None


def transcript_from_json(data: dict[str, Any]) -> str:
    """把字幕 CDN JSON 的 body[].content 拼成转写稿（纯函数，便于 fixture 对拍）。"""
    body = data.get("body") or []
    lines = [
        str(seg.get("content") or "").strip()
        for seg in body
        if isinstance(seg, dict) and seg.get("content")
    ]
    return "\n".join(line for line in lines if line)


def download_subtitle_transcript(subtitle_url: str) -> str:
    """下载字幕 CDN JSON 并拼成转写稿。

    字幕 CDN（aisubtitle.hdslb.com）是带 auth_key 的预签名静态资源，
    与图片下载同模式：直接 httpx + Referer，不经浏览器、不消耗主站风控基线。
    """
    with create_client(headers={"Referer": "https://www.bilibili.com/"}) as client:
        resp = client.get(subtitle_url)
        resp.raise_for_status()
        return transcript_from_json(resp.json())
