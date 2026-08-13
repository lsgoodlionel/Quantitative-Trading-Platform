"""OpenAI Chat Completions 协议适配器。

一个类覆盖 Ollama / OpenAI / DeepSeek / Moonshot / 通义千问（兼容模式）——
这几家的请求体与响应体是同一套，差别只在 base_url 与是否需要 api_key。
唯一走自己格式的是 Anthropic，见 `anthropic.py`。
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.core.llm.base import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT_SECONDS,
    HEALTH_PROBE_MAX_TOKENS,
    HEALTH_PROBE_PROMPT,
    HEALTH_PROBE_TIMEOUT_SECONDS,
    ChatMessage,
    ChatResponse,
    LLMProvider,
    LLMUnavailableError,
    ProviderHealth,
    ToolCall,
    ToolSpec,
)
from app.core.llm.transport import get_json, post_json

_MODEL_LIST_TIMEOUT_SECONDS = 15.0


class OpenAICompatProvider(LLMProvider):
    """OpenAI 兼容端点。``api_key`` 为空即不发 Authorization 头（Ollama 本地即如此）。"""

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        model: str,
        api_key: str | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key or None

    # ── 公开接口 ─────────────────────────────────────────────

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> ChatResponse:
        payload = self._build_payload(messages, tools, temperature, max_tokens)
        body = await post_json(
            f"{self._base_url}/chat/completions",
            headers=self._headers(),
            payload=payload,
            timeout=timeout,
        )
        return self._parse_response(body)

    async def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            await self.chat(
                [ChatMessage(role="user", content=HEALTH_PROBE_PROMPT)],
                max_tokens=HEALTH_PROBE_MAX_TOKENS,
                timeout=HEALTH_PROBE_TIMEOUT_SECONDS,
            )
        except LLMUnavailableError as exc:
            return ProviderHealth(
                ok=False, provider_id=self.provider_id, model=self.model, error=str(exc)
            )
        return ProviderHealth(
            ok=True,
            provider_id=self.provider_id,
            model=self.model,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )

    async def list_models(self) -> tuple[str, ...]:
        body = await get_json(
            f"{self._base_url}/models",
            headers=self._headers(),
            timeout=_MODEL_LIST_TIMEOUT_SECONDS,
        )
        entries = body.get("data")
        if not isinstance(entries, list):
            raise LLMUnavailableError("模型列表响应缺少 data 数组")
        return tuple(
            str(item["id"]) for item in entries if isinstance(item, dict) and item.get("id")
        )

    # ── 请求体构造 ───────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _build_payload(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_encode_message(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = [_encode_tool(t) for t in tools]
        return payload

    # ── 响应解析 ─────────────────────────────────────────────

    def _parse_response(self, body: dict[str, Any]) -> ChatResponse:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMUnavailableError("响应缺少 choices —— 端点可能不是 OpenAI 兼容协议")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise LLMUnavailableError("响应的 choices[0] 缺少 message")

        return ChatResponse(
            content=str(message.get("content") or ""),
            tool_calls=_parse_tool_calls(message.get("tool_calls")),
            model=str(body.get("model") or self.model),
            usage=_parse_usage(body.get("usage")),
        )


# ── 模块级编解码工具 ──────────────────────────────────────────────────────────

def _encode_message(message: ChatMessage) -> dict[str, Any]:
    encoded: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.role == "tool" and message.tool_call_id:
        encoded["tool_call_id"] = message.tool_call_id
    return encoded


def _encode_tool(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters or {"type": "object", "properties": {}},
        },
    }


def _parse_tool_calls(raw: Any) -> tuple[ToolCall, ...]:
    if not isinstance(raw, list):
        return ()
    calls = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        if not isinstance(function, dict) or not function.get("name"):
            continue
        calls.append(
            ToolCall(
                id=str(item.get("id") or f"call_{index}"),
                name=str(function["name"]),
                arguments=_decode_arguments(function.get("arguments")),
            )
        )
    return tuple(calls)


def _decode_arguments(raw: Any) -> dict[str, Any]:
    """OpenAI 把参数塞在一个 JSON **字符串**里；解析失败不吞掉，原样保留供排查。"""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {"__raw__": raw}
    return decoded if isinstance(decoded, dict) else {"__raw__": raw}


def _parse_usage(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    return {k: int(raw[k]) for k in keys if isinstance(raw.get(k), int)}
