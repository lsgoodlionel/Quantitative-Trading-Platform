"""Copilot 测试用的假 provider（V3 Wave B-c / I1）

**不连任何模型**。按脚本逐轮吐回预设的 `ChatResponse`，脚本用完后一直吐最后一条 ——
这样「轮次上限」这类测试可以只给一条无限重复的工具调用。
"""

from __future__ import annotations

from typing import Any

from app.core.llm.base import (
    ChatMessage,
    ChatResponse,
    LLMProvider,
    ProviderHealth,
    ToolCall,
    ToolSpec,
)


def call(name: str, arguments: dict[str, Any], *, content: str = "") -> ChatResponse:
    """一轮「模型要求调用工具」的响应。"""
    return ChatResponse(
        content=content,
        tool_calls=(ToolCall(id=f"c-{name}", name=name, arguments=arguments),),
        model="fake-model",
    )


def raw_call(name: str, raw: str) -> ChatResponse:
    """一轮「参数不是合法 JSON」的响应 —— 网关把原文留在 `__raw__` 里。"""
    return ChatResponse(
        content="",
        tool_calls=(ToolCall(id="c-raw", name=name, arguments={"__raw__": raw}),),
        model="fake-model",
    )


def text_turn(content: str) -> ChatResponse:
    """一轮纯文本响应（没有工具调用）。"""
    return ChatResponse(content=content, model="fake-model")


class FakeProvider(LLMProvider):
    """按脚本回放的 provider。记录每一次收到的消息与工具声明。"""

    def __init__(self, script: list[ChatResponse], *, model: str = "fake-model") -> None:
        self.provider_id = "fake"
        self.model = model
        self._script = list(script)
        self.calls = 0
        self.last_messages: list[ChatMessage] = []
        self.tool_specs_seen: list[list[ToolSpec] | None] = []

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout: float = 60.0,
    ) -> ChatResponse:
        del temperature, max_tokens, timeout
        self.calls += 1
        self.last_messages = list(messages)
        self.tool_specs_seen.append(tools)
        index = min(self.calls - 1, len(self._script) - 1)
        return self._script[index]

    async def health(self) -> ProviderHealth:
        return ProviderHealth(ok=True, provider_id=self.provider_id, model=self.model)

    async def list_models(self) -> tuple[str, ...]:
        return (self.model,)
