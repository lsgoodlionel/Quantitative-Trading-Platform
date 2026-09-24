"""LLM 网关（V3 Wave B-a / I0）—— 多 provider 可切换的统一对话入口。

对外只暴露这些名字；协议细节（OpenAI 兼容 / Anthropic）对调用方不可见。
"""

from app.core.llm.base import (
    ChatMessage,
    ChatResponse,
    LLMError,
    LLMNotConfiguredError,
    LLMProvider,
    LLMUnavailableError,
    ProviderHealth,
    ToolCall,
    ToolSpec,
)
from app.core.llm.registry import PROVIDER_PRESETS, ProviderPreset, get_preset
from app.core.llm.service import ResolvedProvider, resolve_active, resolve_named

__all__ = [
    "PROVIDER_PRESETS",
    "ChatMessage",
    "ChatResponse",
    "LLMError",
    "LLMNotConfiguredError",
    "LLMProvider",
    "LLMUnavailableError",
    "ProviderHealth",
    "ProviderPreset",
    "ResolvedProvider",
    "ToolCall",
    "ToolSpec",
    "get_preset",
    "resolve_active",
    "resolve_named",
]
