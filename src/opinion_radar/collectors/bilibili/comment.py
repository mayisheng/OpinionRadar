"""B 站评论区 UP 主回复纯解析器（字段位置以 api-probe 实测为准，2026-10 探测）。

真实结构（两条样本 + 端点直测对拍）：
- 主楼评论：页面滚动到评论区时拦截 api.bilibili.com/x/v2/reply/wbi/main，
  oid 为 aid（视频，type=1）或 dynamic_id（图文动态，type=17）；
  data.replies[] 每条含 member.mid（字符串）、mid_str、content.message、
  rcount（楼中楼总数）、replies（内嵌预览楼中楼，可能不全）；
  data.cursor.pagination_reply.next_offset / is_end 翻页；
  data.upper.mid 即 UP 主 mid；data.top_replies 为置顶评论。
- UP 主出没信号：主楼的 reply_control.up_reply=true 表示「UP 主在该楼回复过」，
  只需展开这些楼，不必全量拉楼中楼。
- 楼中楼：api.bilibili.com/x/v2/reply/reply?oid=&type=&root=&pn=&ps=20
  免 wbi 签名（登录 cookie 即可），在页面上下文 fetch 直调（同 feed 翻页模式）；
  data.page.{num,size,count} 分页，data.replies[] 为整页楼中楼。
- shadow DOM 点击「展开回复」不触发请求（实测受信/合成点击均无效），故楼中楼走 fetch。

合规红线：只提取 UP 主本人的回复文本与被回复评论的文本上下文，
不采集粉丝昵称等额外信息；原始内容只存本地归档，不推送。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from opinion_radar.collectors.bilibili.feed import raise_for_api_code
from opinion_radar.collectors.exceptions import CollectorResponseError

# 拦截匹配用的 URL 片段（主楼评论列表）
COMMENT_MAIN_API_MARKER = "api.bilibili.com/x/v2/reply/wbi/main"

# 评论对象类型：视频 / 图文动态（oid 分别为 aid 与 dynamic_id）
COMMENT_TYPE_VIDEO = 1
COMMENT_TYPE_DYNAMIC = 17

# body_text 中评论段落的固定标题（幂等标记：已含此标题的正文不再重复拼接）
COMMENT_SECTION_TITLE = "评论区 UP 主回复"

# 评论采集状态（写入 payload["comment_status"]，归档回填据此判断是否还需重试）
COMMENT_OK = "ok"  # 已采集（可能有 / 无 UP 主回复）
COMMENT_EMPTY = "empty"  # 评论区无评论或未加载到主楼响应
COMMENT_ERROR = "error"  # 临时错误，下轮可重试
COMMENT_SKIPPED_PAID = "skipped_paid"  # 未付费充电专属视频，跳过评论采集

# 楼中楼单页条数（与前端一致）
_SUB_REPLY_PAGE_SIZE = 20


class UpReply(BaseModel):
    """UP 主在评论区的一条回复（主楼自评或楼中楼回复）。"""

    rpid: str
    message: str
    ctime: int = 0
    like_count: int = 0
    root_rpid: str | None = None  # 楼中楼所属主楼 rpid；主楼自评为 None
    root_message: str | None = None  # 被回复的主楼文本（上下文，不含昵称）


class FlaggedThread(BaseModel):
    """reply_control.up_reply=true 的主楼：UP 主在其中回复过，需展开楼中楼。"""

    rpid: str
    message: str


class CommentMainPage(BaseModel):
    """主楼评论单页解析结果。"""

    oid: str  # 评论对象 id（视频 aid / 动态 dynamic_id），取自响应 URL 或 replies
    up_replies: list[UpReply]  # UP 主自己的主楼评论（含置顶）
    flagged: list[FlaggedThread]  # 标记了 up_reply 的主楼，待展开
    is_end: bool


def parse_comment_main_page(payload: dict[str, Any], *, up_mid: str) -> CommentMainPage:
    """解析 wbi/main 主楼评论响应；业务码 / 结构异常按风控约定抛出。"""
    raise_for_api_code(payload, api="主楼评论接口")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise CollectorResponseError("主楼评论接口响应缺少 data（接口结构可能已变化）")

    replies = [r for r in data.get("replies") or [] if isinstance(r, dict)]
    top_replies = [r for r in data.get("top_replies") or [] if isinstance(r, dict)]
    cursor = data.get("cursor") or {}

    up_replies: list[UpReply] = []
    seen: set[str] = set()
    for reply in [*top_replies, *replies]:
        rpid = str(reply.get("rpid_str") or reply.get("rpid") or "")
        if not rpid or rpid in seen:
            continue
        if _reply_mid(reply) != up_mid:
            continue
        seen.add(rpid)
        up_replies.append(_to_up_reply(reply, root_rpid=None, root_message=None))

    flagged = [
        FlaggedThread(rpid=str(r.get("rpid_str") or r.get("rpid") or ""), message=_message(r))
        for r in replies
        if (r.get("reply_control") or {}).get("up_reply")
        and str(r.get("rpid_str") or r.get("rpid") or "")
    ]

    oid = ""
    for reply in [*top_replies, *replies]:
        oid = str(reply.get("oid_str") or reply.get("oid") or "")
        if oid:
            break

    return CommentMainPage(
        oid=oid,
        up_replies=up_replies,
        flagged=flagged,
        is_end=bool(cursor.get("is_end")),
    )


def parse_sub_replies(
    payload: dict[str, Any],
    *,
    up_mid: str,
    root_rpid: str,
    root_message: str,
) -> tuple[list[UpReply], bool]:
    """解析 reply/reply 楼中楼响应，返回（UP 主回复列表, 是否还有下一页）。"""
    raise_for_api_code(payload, api="楼中楼评论接口")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise CollectorResponseError("楼中楼评论接口响应缺少 data（接口结构可能已变化）")
    page = data.get("page") or {}
    replies = [r for r in data.get("replies") or [] if isinstance(r, dict)]
    up_replies = [
        _to_up_reply(r, root_rpid=root_rpid, root_message=root_message)
        for r in replies
        if _reply_mid(r) == up_mid
    ]
    num = int(page.get("num") or 1)
    size = int(page.get("size") or _SUB_REPLY_PAGE_SIZE)
    count = int(page.get("count") or 0)
    has_more = num * size < count
    return up_replies, has_more


def build_sub_reply_url(oid: str, comment_type: int, root_rpid: str, pn: int) -> str:
    """构造楼中楼请求 URL（免签名，页面上下文 fetch 使用）。"""
    return (
        "https://api.bilibili.com/x/v2/reply/reply"
        f"?oid={oid}&type={comment_type}&root={root_rpid}&ps={_SUB_REPLY_PAGE_SIZE}&pn={pn}"
    )


def build_reply_section(up_replies: list[UpReply]) -> str | None:
    """把 UP 主回复渲染成「评论区 UP 主回复」段落，拼入 body_text；无回复返回 None。

    段落首行即固定标题 COMMENT_SECTION_TITLE，作为归档合并时的幂等标记。
    """
    if not up_replies:
        return None
    lines = [f"## {COMMENT_SECTION_TITLE}", ""]
    for reply in up_replies:
        if reply.root_message:
            context = reply.root_message.replace("\n", " ").strip()
            lines.append(f"> 原评论：{context}")
        lines.append(reply.message.strip())
        lines.append("")
    return "\n".join(lines).strip()


def _reply_mid(reply: dict[str, Any]) -> str:
    """评论者 mid（member.mid 为字符串；mid_str 兜底）。"""
    member = reply.get("member") or {}
    return str(member.get("mid") or reply.get("mid_str") or reply.get("mid") or "")


def _message(reply: dict[str, Any]) -> str:
    return str((reply.get("content") or {}).get("message") or "").strip()


def _to_up_reply(
    reply: dict[str, Any], *, root_rpid: str | None, root_message: str | None
) -> UpReply:
    return UpReply(
        rpid=str(reply.get("rpid_str") or reply.get("rpid") or ""),
        message=_message(reply),
        ctime=int(reply.get("ctime") or 0),
        like_count=int(reply.get("like") or 0),
        root_rpid=root_rpid,
        root_message=root_message,
    )
