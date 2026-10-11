"""统一 HTTP 客户端工厂。

所有外部请求（B站 / Ollama / Server酱）必须经此创建客户端，
保证统一 UA、超时与跳转策略，便于测试时整体 mock。
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

# 桌面浏览器 UA，降低被风控识别为脚本的概率
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def create_client(
    *,
    timeout: float = 15.0,
    headers: Mapping[str, str] | None = None,
    cookies: Mapping[str, str] | None = None,
) -> httpx.Client:
    """创建同步 httpx 客户端。"""
    merged_headers = {"User-Agent": USER_AGENT, **dict(headers or {})}
    return httpx.Client(
        timeout=timeout,
        headers=merged_headers,
        cookies=dict(cookies or {}),
        follow_redirects=True,
    )
