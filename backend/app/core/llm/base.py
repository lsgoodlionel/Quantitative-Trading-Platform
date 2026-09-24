"""LLM 网关 —— 协议无关的核心抽象（V3 Wave B-a / I0）

这里只放「与厂商无关」的东西：消息 / 工具 / 响应的数据形状，以及 Provider 契约。
具体协议实现见 `openai_compat.py`（Ollama / OpenAI / DeepSeek / Moonshot / 通义）
与 `anthropic.py`（Anthropic Messages API）。

错误分层是本模块最重要的设计：

- ``LLMNotConfiguredError`` → 一个 provider 都没配 → HTTP **501**（功能未启用）
- ``LLMUnavailableError``   → 配了但连不上 / key 无效 / 模型不存在 → HTTP **503**

两者都报 501 会让「Ollama 没启动」看起来像「平台不支持 AI」，是完全不同的排查方向。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

# ── 常量 ─────────────────────────────────────────────────────────────────────

MessageRole = Literal["system", "user", "assistant", "tool"]
ProviderKind = Literal["openai_compat", "anthropic"]

DEFAULT_TEMPERATURE = 0.2
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_TOKENS = 2048

# 连通性探测用：只要能拿回一个 token 就说明 base_url / key / 模型名三者都对
HEALTH_PROBE_MAX_TOKENS = 16
HEALTH_PROBE_TIMEOUT_SECONDS = 20.0
HEALTH_PROBE_PROMPT = "ping"


# ── 异常 ─────────────────────────────────────────────────────────────────────

class LLMError(Exception):
    """LLM 网关的错误基类。"""


class LLMNotConfiguredError(LLMError):
    """一个可用的 provider 都没有 —— 功能未启用（501）。"""


class LLMUnavailableError(LLMError):
    """provider 配了但用不了：连不上 / 鉴权失败 / 模型不存在（503）。"""


# ── 数据形状 ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ChatMessage:
    """一条对话消息。``tool_call_id`` 仅在 role="tool" 时有意义。"""

    role: MessageRole
    content: str
    tool_call_id: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    """工具声明。``parameters`` 是 JSON Schema，两种协议都直接透传。"""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCall:
    """模型请求调用的一次工具。``arguments`` 已解析为 dict。"""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatResponse:
    """归一化后的对话响应 —— 调用方不需要知道背后是哪家协议。"""

    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    model: str = ""
    # prompt_tokens / completion_tokens / total_tokens，拿不到就留空 dict
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderHealth:
    """连通性探测结果。ok=False 时 ``error`` 必定有值。"""

    ok: bool
    provider_id: str
    model: str | None = None
    latency_ms: float | None = None
    error: str | None = None


# ── Provider 契约 ─────────────────────────────────────────────────────────────

class LLMProvider(ABC):
    """一个「可用的模型端点」——(协议, base_url, key, model) 四元组的运行期封装。"""

    provider_id: str
    model: str

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> ChatResponse:
        """发起一次非流式对话。失败一律抛 ``LLMUnavailableError``。"""

    @abstractmethod
    async def health(self) -> ProviderHealth:
        """真实发一次最小请求做连通性探测（不是 ping base_url）。"""

    @abstractmethod
    async def list_models(self) -> tuple[str, ...]:
        """拉取该端点当前可用的模型名。失败抛 ``LLMUnavailableError``。"""
