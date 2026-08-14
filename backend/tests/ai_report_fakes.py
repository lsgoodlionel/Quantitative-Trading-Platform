"""AI 报告测试用的公共替身（V3 Wave C-a / I4+I5）

**不连任何模型**：provider 一律复用 `tests/copilot_fakes.py` 的脚本回放假货，
这里只补两样东西 —— 把 dict 包成一轮 JSON 响应，以及构造 `resolve_active` 的返回值。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from app.core.llm.base import ChatMessage, ChatResponse
from tests.copilot_fakes import FakeProvider


def json_turn(payload: dict[str, Any], *, model: str = "fake-model") -> ChatResponse:
    """一轮「模型吐出合法 JSON」的响应。"""
    return ChatResponse(content=json.dumps(payload, ensure_ascii=False), model=model)


def fenced_turn(payload: dict[str, Any]) -> ChatResponse:
    """一轮被 ```json 围栏裹起来的响应 —— 本地小模型的常见形态。"""
    body = json.dumps(payload, ensure_ascii=False)
    return ChatResponse(content=f"好的，结果如下：\n```json\n{body}\n```", model="fake-model")


def junk_turn(text: str = "抱歉，我不太确定。") -> ChatResponse:
    """一轮压根不是 JSON 的响应。"""
    return ChatResponse(content=text, model="fake-model")


@dataclass(frozen=True)
class FakePreset:
    id: str = "ollama"
    label: str = "Ollama"


@dataclass(frozen=True)
class FakeResolved:
    provider: Any
    preset: FakePreset = FakePreset()
    model: str = "qwen2.5:14b"
    explicit: bool = True


def use_provider(
    monkeypatch: pytest.MonkeyPatch, module: Any, provider: FakeProvider
) -> FakeProvider:
    """把端点模块里的 `resolve_active` 换成返回这个假 provider。"""

    async def _resolve(redis: Any, *, model_override: str | None = None) -> FakeResolved:
        del redis, model_override
        return FakeResolved(provider=provider)

    monkeypatch.setattr(module, "resolve_active", _resolve)
    return provider


def fail_resolve(monkeypatch: pytest.MonkeyPatch, module: Any, exc: Exception) -> None:
    """让 `resolve_active` 抛指定异常（未配置 / 连不上）。"""

    async def _resolve(redis: Any, *, model_override: str | None = None):
        del redis, model_override
        raise exc

    monkeypatch.setattr(module, "resolve_active", _resolve)


def prompt_text(provider: FakeProvider) -> str:
    """provider 最后一次收到的全部消息文本 —— 断言提示词内容用。"""
    return "\n".join(_content(m) for m in provider.last_messages)


def _content(message: ChatMessage) -> str:
    return message.content
