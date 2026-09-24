"""Anthropic Messages API 适配器。

与 OpenAI 协议的四处实质差异（这也是本文件存在的全部理由）：

1. system 不是一条 message，而是请求体的**独立顶层字段**
2. 鉴权用 ``x-api-key`` 头 + 必填的 ``anthropic-version``
3. ``max_tokens`` 是必填项，不是可选项
4. 响应是 content **块数组**（text / tool_use 混排），不是单个 message.content；
   工具参数在 ``input`` 里已经是 dict，不像 OpenAI 那样是 JSON 字符串
"""

from __future__ import annotations

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

ANTHROPIC_VERSION = "2023-06-01"
_MODEL_LIST_TIMEOUT_SECONDS = 15.0


class AnthropicProvider(LLMProvider):
    """Anthropic Messages 端点。"""

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
            f"{self._base_url}/messages",
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
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
        }
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    def _build_payload(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        system_text, turns = _split_system(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": turns,
        }
        if system_text:
            payload["system"] = system_text
        if tools:
            payload["tools"] = [_encode_tool(t) for t in tools]
        return payload

    # ── 响应解析 ─────────────────────────────────────────────

    def _parse_response(self, body: dict[str, Any]) -> ChatResponse:
        blocks = body.get("content")
        if not isinstance(blocks, list):
            raise LLMUnavailableError("响应缺少 content 块数组 —— 端点可能不是 Anthropic 协议")

        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                texts.append(str(block["text"]))
            elif block.get("type") == "tool_use" and block.get("name"):
                arguments = block.get("input")
                calls.append(
                    ToolCall(
                        id=str(block.get("id") or f"call_{len(calls)}"),
                        name=str(block["name"]),
                        arguments=arguments if isinstance(arguments, dict) else {},
                    )
                )

        return ChatResponse(
            content="\n".join(texts),
            tool_calls=tuple(calls),
            model=str(body.get("model") or self.model),
            usage=_parse_usage(body.get("usage")),
        )


# ── 模块级编解码工具 ──────────────────────────────────────────────────────────

def _split_system(messages: list[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
    """把 system 消息抽成独立字段，其余按 Anthropic 的 turn 结构编码。"""
    system_parts = [m.content for m in messages if m.role == "system" and m.content]
    turns = [_encode_turn(m) for m in messages if m.role != "system"]
    return "\n\n".join(system_parts), turns


def _encode_turn(message: ChatMessage) -> dict[str, Any]:
    if message.role == "tool":
        # 工具结果在 Anthropic 里是一条 user 消息中的 tool_result 块
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id or "",
                    "content": message.content,
                }
            ],
        }
    return {"role": message.role, "content": message.content}


def _encode_tool(tool: ToolSpec) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.parameters or {"type": "object", "properties": {}},
    }


def _parse_usage(raw: Any) -> dict[str, int]:
    """归一化到 OpenAI 的 token 字段名，调用方不必区分协议。"""
    if not isinstance(raw, dict):
        return {}
    prompt = raw.get("input_tokens")
    completion = raw.get("output_tokens")
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return {}
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
