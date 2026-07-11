"""
通知配置的 Redis 持久化。

键（仿 broker_config:* 约定）：
- notify:config          哈希，字段 data = JSON(NotifyConfig)（密钥明文，绝不返回）
- notify:config:version  int，incr 触发 dispatcher 配置重载

PUT 时空密钥 = 保持原值（与 Alpaca 表单一致）：merge_secrets 合并旧密钥。
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import redis.asyncio as aioredis

from app.notify.config import NotifyConfig, default_notify_config

logger = logging.getLogger(__name__)

CONFIG_KEY = "notify:config"
CONFIG_VERSION_KEY = "notify:config:version"
_DATA_FIELD = "data"


async def load_config(redis: aioredis.Redis) -> NotifyConfig:
    """从 Redis 读取通知配置（含明文密钥），缺失则返回默认。"""
    try:
        raw = await redis.hget(CONFIG_KEY, _DATA_FIELD)
    except Exception:
        logger.exception("Failed to read notify config from Redis")
        return default_notify_config()
    if not raw:
        return default_notify_config()
    try:
        return NotifyConfig.model_validate(json.loads(raw))
    except Exception:
        logger.exception("Corrupt notify config, falling back to default")
        return default_notify_config()


def merge_secrets(incoming: NotifyConfig, stored: NotifyConfig) -> NotifyConfig:
    """空密钥（bot_token / secret_value）沿用已存储的旧值。"""
    stored_by_id = {ch.id: ch for ch in stored.channels}
    merged_channels = []
    for ch in incoming.channels:
        prev = stored_by_id.get(ch.id)
        new_ch = ch.model_copy(deep=True)
        if new_ch.telegram is not None and not new_ch.telegram.bot_token:
            if prev is not None and prev.telegram is not None:
                new_ch.telegram.bot_token = prev.telegram.bot_token
        if new_ch.webhook is not None and not new_ch.webhook.secret_value:
            if prev is not None and prev.webhook is not None:
                new_ch.webhook.secret_value = prev.webhook.secret_value
                # secret_header 不在脱敏状态中回传，空值时一并沿用旧值避免丢失
                if not new_ch.webhook.secret_header:
                    new_ch.webhook.secret_header = prev.webhook.secret_header
        merged_channels.append(new_ch)
    return incoming.model_copy(update={"channels": merged_channels})


async def save_config(redis: aioredis.Redis, config: NotifyConfig) -> NotifyConfig:
    """合并旧密钥后持久化，自增版本号，返回合并后的配置。"""
    stored = await load_config(redis)
    merged = merge_secrets(config, stored)
    await redis.hset(CONFIG_KEY, _DATA_FIELD, json.dumps(merged.to_dict()))
    await redis.incr(CONFIG_VERSION_KEY)
    return merged


async def read_version(redis: aioredis.Redis) -> Optional[int]:
    try:
        v = await redis.get(CONFIG_VERSION_KEY)
        return int(v) if v is not None else None
    except Exception:
        return None
