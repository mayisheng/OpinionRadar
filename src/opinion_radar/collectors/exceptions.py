"""采集层自定义异常（编码规范第 4 节：预期外的平台响应抛项目内异常）。

管线在边界捕获这些异常：记录日志、置 failed / 中止本轮，不能让整轮崩溃。
风控类异常触发熔断，禁止立即重试硬闯（ADR-0005）。
"""

from __future__ import annotations


class CollectorError(Exception):
    """采集层所有异常的基类。"""


class BrowserRuntimeMissingError(CollectorError):
    """playwright 未安装或 Chromium 运行时缺失（未安装 browser extra）。"""


class LoginRequiredError(CollectorError):
    """登录态缺失或已失效，需要重新执行 ``opinion-radar bilibili-login``。"""


class RiskControlBlocked(CollectorError):
    """命中平台风控（验证码 / -352 / -412 / 风控跳转）：立即熔断冷却，不得重试。"""


class CollectorResponseError(CollectorError):
    """平台返回了未预期的业务码或结构（接口改版等）。"""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class CollectorTimeout(CollectorError):
    """等待页面 / 接口响应超时（网络慢或页面结构变化）。"""
