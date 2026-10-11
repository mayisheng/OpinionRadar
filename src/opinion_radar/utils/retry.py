"""外部请求的统一重试策略：指数退避 + 随机抖动。

用法：
    @http_retry(attempts=3)
    def call_api() -> ...: ...
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

T = TypeVar("T")

DecoratedFunc = Callable[[Callable[..., T]], Callable[..., T]]


def http_retry(*, attempts: int = 3, max_wait: float = 30.0) -> DecoratedFunc[T]:
    """对网络传输错误重试；HTTP 4xx 等业务错误不重试（由调用方判断）。"""
    return retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(attempts),
        wait=wait_exponential_jitter(max=max_wait),
        reraise=True,
    )
