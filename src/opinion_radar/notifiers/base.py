"""推送层抽象：Notifier 接口。

首期实现 Server酱（微信）；后续新增 Bark / Telegram / 飞书时实现本接口，
由 config.notify.channel 选择。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class PushResult(BaseModel):
    """一次推送的结果，落库到 push_logs 用于幂等与排查。"""

    ok: bool
    channel: str
    message_id: str | None = None
    raw: str | None = None


class Notifier(ABC):
    """消息推送渠道。"""

    channel: str

    @abstractmethod
    def send(self, title: str, content: str) -> PushResult:
        """发送一条 Markdown 消息。失败时抛异常或返回 ok=False。"""
