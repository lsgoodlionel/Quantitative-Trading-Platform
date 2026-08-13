"""LLM provider 配置存储（Redis hash）。

形态刻意与券商配置（`app/api/v1/endpoints/broker_config.py`）保持一致：
一个 provider 一个 hash，读取时用同款掩码（首 2 位 + 掩码 + 末 4 位）。

⚠️ **安全现状说明（刻意为之，不是遗漏）**

本模块把 ``api_key`` **明文**存在 Redis 里，不做静态加密。理由：项目里能直接
动钱的券商凭证（Alpaca / 富途）目前同样是明文存 Redis。只给 LLM 密钥单独加密
会造成「这里是安全的」的错觉，而真正高价值的凭证反而裸奔 —— 虚假的安心比
明确的现状更危险。

静态加密是横切关注点，应当单独立项、一次覆盖所有凭证（券商 + 通知渠道 + LLM）。
在那之前，本模块的防线是：**读取端点永远只回掩码**，完整 key 只在服务端内存里用。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# Redis 键前缀。与 broker_config 的 "broker_config:{gateway}" 同构。
KEY_PREFIX = "llm_config"
# 当前生效的 provider/model。用 __active__ 保证不会和任何 provider id 撞名。
ACTIVE_KEY = f"{KEY_PREFIX}:__active__"

_FIELD_BASE_URL = "base_url"
_FIELD_API_KEY = "api_key"
_FIELD_DEFAULT_MODEL = "default_model"
_FIELD_PROVIDER = "provider_id"
_FIELD_MODEL = "model"

# 掩码保留的头尾长度：太短的串直接整体打码，避免几乎等于明文
_MASK_HEAD = 2
_MASK_TAIL = 4
_MASK_MIN_LENGTH = _MASK_HEAD + _MASK_TAIL


@dataclass(frozen=True)
class ProviderConfig:
    """一个 provider 的持久化配置（含完整 key，只在服务端内存流转）。"""

    provider_id: str
    base_url: str
    default_model: str
    api_key: str | None = None

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    @property
    def masked_key(self) -> str | None:
        return mask_secret(self.api_key) if self.api_key else None


@dataclass(frozen=True)
class ActiveSelection:
    """当前生效的 provider + 模型。"""

    provider_id: str
    model: str


def mask_secret(value: str) -> str:
    """显示首 2 位 + 中间掩码 + 末 4 位，如 ``sk••••••••••••5678``。"""
    if len(value) <= _MASK_MIN_LENGTH:
        return "•" * 6
    return value[:_MASK_HEAD] + "•" * (len(value) - _MASK_MIN_LENGTH) + value[-_MASK_TAIL:]


def _redis_key(provider_id: str) -> str:
    return f"{KEY_PREFIX}:{provider_id}"


# ── provider 配置读写 ─────────────────────────────────────────────────────────

async def load_config(redis: Any, provider_id: str) -> ProviderConfig | None:
    """读一个 provider 的配置；没保存过返回 None。"""
    raw = await redis.hgetall(_redis_key(provider_id))
    if not raw:
        return None
    return ProviderConfig(
        provider_id=provider_id,
        base_url=raw.get(_FIELD_BASE_URL, ""),
        default_model=raw.get(_FIELD_DEFAULT_MODEL, ""),
        api_key=raw.get(_FIELD_API_KEY) or None,
    )


async def load_all_configs(
    redis: Any, provider_ids: Sequence[str]
) -> dict[str, ProviderConfig]:
    """按给定 id 逐个读取（不用 SCAN：id 集合是有限且已知的）。"""
    configs: dict[str, ProviderConfig] = {}
    for provider_id in provider_ids:
        config = await load_config(redis, provider_id)
        if config is not None:
            configs[provider_id] = config
    return configs


async def save_config(
    redis: Any,
    provider_id: str,
    *,
    base_url: str,
    default_model: str,
    api_key: str | None = None,
) -> ProviderConfig:
    """
    保存配置并返回落盘后的结果。

    ⚠️ ``api_key`` 传 None 或空串 = **保持原值**，不是清空。
    用户改 base_url 时不该被迫重新粘一遍密钥；清空请走 `delete_config`。
    """
    existing = await load_config(redis, provider_id)
    effective_key = api_key or (existing.api_key if existing else None)

    payload = {
        _FIELD_BASE_URL: base_url,
        _FIELD_DEFAULT_MODEL: default_model,
    }
    if effective_key:
        payload[_FIELD_API_KEY] = effective_key
    await redis.hset(_redis_key(provider_id), mapping=payload)

    return ProviderConfig(
        provider_id=provider_id,
        base_url=base_url,
        default_model=default_model,
        api_key=effective_key,
    )


async def delete_config(redis: Any, provider_id: str) -> bool:
    """彻底删除该 provider 的配置（含密钥）。返回是否真的删掉了东西。"""
    deleted = await redis.delete(_redis_key(provider_id))
    return bool(deleted)


# ── 生效选择读写 ──────────────────────────────────────────────────────────────

async def load_active(redis: Any) -> ActiveSelection | None:
    """读用户显式指定的生效 provider/model；没设过返回 None。"""
    raw = await redis.hgetall(ACTIVE_KEY)
    if not raw or not raw.get(_FIELD_PROVIDER):
        return None
    return ActiveSelection(
        provider_id=raw[_FIELD_PROVIDER],
        model=raw.get(_FIELD_MODEL, ""),
    )


async def save_active(redis: Any, provider_id: str, model: str) -> ActiveSelection:
    """写入生效选择。model 不做白名单校验 —— 用户填什么就是什么。"""
    await redis.hset(
        ACTIVE_KEY, mapping={_FIELD_PROVIDER: provider_id, _FIELD_MODEL: model}
    )
    return ActiveSelection(provider_id=provider_id, model=model)


async def clear_active(redis: Any) -> None:
    """清除生效选择，回退到「按预设顺序自动挑第一个可用的」。"""
    await redis.delete(ACTIVE_KEY)
