"""网关服务层 —— 把「存储里的配置」解析成「一个能用的 provider」。

这一层是 501 与 503 的分界点：

- 解析不出任何**可用**的 provider   → `LLMNotConfiguredError` → 501（功能未启用）
- 解析出来了但调用时连不上 / 401  → `LLMUnavailableError`   → 503（依赖挂了）

「可用」的判定：有 base_url，且（不需要密钥 或 已填密钥），且有模型名。
Ollama 不需要密钥，所以只要填了地址与模型就算可用 —— 这正是「无密钥也能用」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.llm.base import LLMNotConfiguredError, LLMProvider
from app.core.llm.config_store import (
    ActiveSelection,
    ProviderConfig,
    load_active,
    load_all_configs,
)
from app.core.llm.registry import PRESET_IDS, ProviderPreset, build_provider, get_preset

_NOT_CONFIGURED_HINT = (
    "尚未配置任何可用的模型服务。推荐先安装 Ollama 并在「模型管理」页填入访问地址"
    "（默认 http://localhost:11434/v1），本地模型无需 API 密钥。"
)


@dataclass(frozen=True)
class ResolvedProvider:
    """解析结果：provider 实例 + 它是怎么被选中的（供前端展示）。"""

    provider: LLMProvider
    preset: ProviderPreset
    model: str
    explicit: bool  # True = 用户显式指定；False = 按预设顺序自动挑的


def is_ready(config: ProviderConfig, preset: ProviderPreset) -> bool:
    """该配置是否已经足够发起一次请求。"""
    if not config.base_url or not config.default_model:
        return False
    return config.has_key or not preset.requires_key


async def resolve_active(
    redis: Any, *, model_override: str | None = None
) -> ResolvedProvider:
    """
    解析当前生效的 provider。没有任何可用 provider 时抛 `LLMNotConfiguredError`。

    优先级：用户显式指定的 active > 预设顺序里第一个可用的（Ollama 排第一）。
    """
    configs = await load_all_configs(redis, PRESET_IDS)
    ready = _ready_pairs(configs)
    if not ready:
        raise LLMNotConfiguredError(_NOT_CONFIGURED_HINT)

    selection = await load_active(redis)
    chosen, explicit = _choose(ready, selection)
    config, preset = chosen
    model = model_override or _effective_model(config, selection, explicit)

    return ResolvedProvider(
        provider=build_provider(
            preset, base_url=config.base_url, model=model, api_key=config.api_key
        ),
        preset=preset,
        model=model,
        explicit=explicit,
    )


async def resolve_named(
    redis: Any, provider_id: str, *, model_override: str | None = None
) -> ResolvedProvider:
    """
    解析指定的 provider（供 `/test` 与 `/models` 使用）。

    与 `resolve_active` 不同：这里的「没配」是针对单个 provider 的，
    同样抛 `LLMNotConfiguredError`，由端点翻译成合适的状态码。
    """
    preset = get_preset(provider_id)
    if preset is None:
        raise LLMNotConfiguredError(f"未知的模型服务商：{provider_id}")

    configs = await load_all_configs(redis, (provider_id,))
    config = configs.get(provider_id)
    if config is None or not config.base_url:
        raise LLMNotConfiguredError(f"{preset.label} 尚未配置，请先保存访问地址。")

    model = model_override or config.default_model
    if not model:
        raise LLMNotConfiguredError(f"{preset.label} 尚未指定默认模型。")

    return ResolvedProvider(
        provider=build_provider(
            preset, base_url=config.base_url, model=model, api_key=config.api_key
        ),
        preset=preset,
        model=model,
        explicit=True,
    )


# ── 内部 ─────────────────────────────────────────────────────────────────────

def _ready_pairs(
    configs: dict[str, ProviderConfig],
) -> list[tuple[ProviderConfig, ProviderPreset]]:
    """按预设顺序返回所有「立刻可用」的 (配置, 预设) 对。"""
    pairs = []
    for provider_id in PRESET_IDS:
        config = configs.get(provider_id)
        preset = get_preset(provider_id)
        if config is not None and preset is not None and is_ready(config, preset):
            pairs.append((config, preset))
    return pairs


def _choose(
    ready: list[tuple[ProviderConfig, ProviderPreset]],
    selection: ActiveSelection | None,
) -> tuple[tuple[ProviderConfig, ProviderPreset], bool]:
    """命中用户选择就用它，否则回退到清单里第一个可用的。"""
    if selection is not None:
        for pair in ready:
            if pair[0].provider_id == selection.provider_id:
                return pair, True
    return ready[0], False


def _effective_model(
    config: ProviderConfig, selection: ActiveSelection | None, explicit: bool
) -> str:
    """显式选择里带了模型名就用它，否则用该 provider 的默认模型。"""
    if explicit and selection is not None and selection.model:
        return selection.model
    return config.default_model
