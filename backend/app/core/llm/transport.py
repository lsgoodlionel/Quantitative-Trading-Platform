"""LLM 网关的 HTTP 传输层 —— 两种协议共用。

只做一件事：把 httpx 的各种失败形态统一翻译成 ``LLMUnavailableError``，
并从厂商各不相同的错误体里挖出人能看懂的那句话。

`build_client` 单独成函数是刻意的**测试缝**：单测 monkeypatch 它返回一个挂了
``httpx.MockTransport`` 的客户端，从而在不连任何外部服务的前提下覆盖请求体构造
与响应解析。生产代码路径不变。
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.llm.base import LLMUnavailableError

# 错误体过长时截断，避免把整页 HTML 塞进前端提示框
_MAX_ERROR_CHARS = 300


def build_client(timeout: float) -> httpx.AsyncClient:
    """构造 HTTP 客户端。测试通过 monkeypatch 本函数注入 MockTransport。"""
    return httpx.AsyncClient(timeout=timeout)


async def post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    """POST 一个 JSON 并返回解析后的 dict；任何失败都抛 LLMUnavailableError。"""
    try:
        async with build_client(timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise LLMUnavailableError(_connect_error_message(url, exc)) from exc
    return _parse_response(url, response)


async def get_json(
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    """GET 一个 JSON 并返回解析后的 dict；任何失败都抛 LLMUnavailableError。"""
    try:
        async with build_client(timeout) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise LLMUnavailableError(_connect_error_message(url, exc)) from exc
    return _parse_response(url, response)


# ── 内部 ─────────────────────────────────────────────────────────────────────

def _connect_error_message(url: str, exc: httpx.HTTPError) -> str:
    detail = str(exc) or exc.__class__.__name__
    return f"无法连接 {url}：{detail}"


def _parse_response(url: str, response: httpx.Response) -> dict[str, Any]:
    if response.status_code >= 400:
        raise LLMUnavailableError(
            f"{url} 返回 HTTP {response.status_code}：{extract_error_message(response)}"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise LLMUnavailableError(f"{url} 返回的不是合法 JSON（HTTP {response.status_code}）") from exc
    if not isinstance(body, dict):
        raise LLMUnavailableError(f"{url} 返回的 JSON 顶层不是对象")
    return body


def extract_error_message(response: httpx.Response) -> str:
    """从厂商错误体里取出可读信息：OpenAI 系是 error.message，Ollama 是 error。"""
    try:
        body = response.json()
    except ValueError:
        return _truncate(response.text)

    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("type")
            if message:
                return _truncate(str(message))
        if isinstance(error, str) and error:
            return _truncate(error)
        for key in ("message", "detail", "msg"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return _truncate(value)
    return _truncate(response.text)


def _truncate(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return "（无错误详情）"
    if len(cleaned) <= _MAX_ERROR_CHARS:
        return cleaned
    return cleaned[:_MAX_ERROR_CHARS] + "…"
