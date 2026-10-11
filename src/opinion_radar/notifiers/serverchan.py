"""Server酱推送骨架（https://sct.ftqq.com/，微信接收）。

TODO(迭代 001): POST https://sct.ftqq.com/{SENDKEY}.send，字段 title / desp。
TODO(迭代 004): PushPlus 同形态备选渠道，按 config.notify.channel 切换。
"""

from __future__ import annotations

from opinion_radar.notifiers.base import Notifier, PushResult


class ServerChanNotifier(Notifier):
    channel = "serverchan"

    def __init__(self, sendkey: str) -> None:
        self.sendkey = sendkey

    def send(self, title: str, content: str) -> PushResult:
        raise NotImplementedError("Server酱推送将在迭代 001 实现")
