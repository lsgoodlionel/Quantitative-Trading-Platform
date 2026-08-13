"""Provider 预设清单与运行期构建。

产品方向：**本地 Ollama 优先**。它排在清单第一位、无需密钥、默认地址可改 ——
「无密钥也能用」是第一等公民而非降级路径。

⚠️ ``suggested_models`` 是**建议不是白名单**。用户填任意模型名都必须放行：
本地 Ollama 拉了什么模型只有用户自己知道，写死清单会直接挡住正常使用。
清单只用于前端下拉框的默认候选，任何校验逻辑都不得引用它做准入判断。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.llm.anthropic import AnthropicProvider
from app.core.llm.base import LLMProvider, ProviderKind
from app.core.llm.openai_compat import OpenAICompatProvider


@dataclass(frozen=True)
class ProviderPreset:
    """一家厂商的开箱默认值。"""

    id: str
    label: str
    kind: ProviderKind
    default_base_url: str
    requires_key: bool
    suggested_models: tuple[str, ...]
    # 前端用来引导用户去哪拿 key / 装什么
    doc_url: str = ""
    # 端点是否支持列模型（GET {base_url}/models）
    supports_model_listing: bool = True


PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        id="ollama",
        label="Ollama（本地）",
        kind="openai_compat",
        default_base_url="http://localhost:11434/v1",
        requires_key=False,
        suggested_models=("qwen2.5:14b", "llama3.1:8b", "deepseek-r1:14b"),
        doc_url="https://ollama.com/download",
    ),
    ProviderPreset(
        id="openai",
        label="OpenAI",
        kind="openai_compat",
        default_base_url="https://api.openai.com/v1",
        requires_key=True,
        suggested_models=("gpt-4o", "gpt-4o-mini"),
        doc_url="https://platform.openai.com/api-keys",
    ),
    ProviderPreset(
        id="deepseek",
        label="DeepSeek",
        kind="openai_compat",
        default_base_url="https://api.deepseek.com/v1",
        requires_key=True,
        suggested_models=("deepseek-chat", "deepseek-reasoner"),
        doc_url="https://platform.deepseek.com/api_keys",
    ),
    ProviderPreset(
        id="moonshot",
        label="Moonshot（月之暗面）",
        kind="openai_compat",
        default_base_url="https://api.moonshot.cn/v1",
        requires_key=True,
        suggested_models=("moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"),
        doc_url="https://platform.moonshot.cn/console/api-keys",
    ),
    ProviderPreset(
        id="dashscope",
        label="通义千问（DashScope）",
        kind="openai_compat",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        requires_key=True,
        suggested_models=("qwen-max", "qwen-plus", "qwen-turbo"),
        doc_url="https://bailian.console.aliyun.com/",
    ),
    ProviderPreset(
        id="anthropic",
        label="Anthropic",
        kind="anthropic",
        default_base_url="https://api.anthropic.com/v1",
        requires_key=True,
        suggested_models=(
            "claude-sonnet-4-5",
            "claude-opus-4-5",
            "claude-haiku-4-5",
        ),
        doc_url="https://console.anthropic.com/settings/keys",
    ),
)

_PRESET_BY_ID: dict[str, ProviderPreset] = {p.id: p for p in PROVIDER_PRESETS}

# 预设顺序即优先级顺序：没显式指定 active 时按这个顺序挑第一个可用的
PRESET_IDS: tuple[str, ...] = tuple(p.id for p in PROVIDER_PRESETS)


def get_preset(provider_id: str) -> ProviderPreset | None:
    """按 id 取预设；未知 id 返回 None（由调用方决定报 404 还是忽略）。"""
    return _PRESET_BY_ID.get(provider_id)


def build_provider(
    preset: ProviderPreset,
    *,
    base_url: str,
    model: str,
    api_key: str | None = None,
) -> LLMProvider:
    """按预设声明的协议种类构造 provider 实例。"""
    if preset.kind == "anthropic":
        return AnthropicProvider(
            provider_id=preset.id, base_url=base_url, model=model, api_key=api_key
        )
    return OpenAICompatProvider(
        provider_id=preset.id, base_url=base_url, model=model, api_key=api_key
    )
