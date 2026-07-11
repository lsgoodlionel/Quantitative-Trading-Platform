"""
防护配置 / 活跃锁的 Redis 持久化。

键（仿 broker_config:* 约定）：
- protections:config          哈希，字段 data = JSON(ProtectionsConfig)
- protections:config:version  int，incr 触发 manager 热重载
- protections:locks           哈希，字段 lock_id = JSON(ActiveLock)
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import redis.asyncio as aioredis

from app.oms.protections.base import ActiveLock
from app.oms.protections.config import ProtectionsConfig, default_protections_config

logger = logging.getLogger(__name__)

CONFIG_KEY = "protections:config"
CONFIG_VERSION_KEY = "protections:config:version"
LOCKS_KEY = "protections:locks"
_DATA_FIELD = "data"


async def load_config(redis: aioredis.Redis) -> ProtectionsConfig:
    """从 Redis 读取防护配置，缺失则返回默认。"""
    try:
        raw = await redis.hget(CONFIG_KEY, _DATA_FIELD)
    except Exception:
        logger.exception("Failed to read protections config from Redis")
        return default_protections_config()
    if not raw:
        return default_protections_config()
    try:
        return ProtectionsConfig.model_validate(json.loads(raw))
    except Exception:
        logger.exception("Corrupt protections config, falling back to default")
        return default_protections_config()


async def save_config(redis: aioredis.Redis, config: ProtectionsConfig) -> int:
    """持久化配置并自增版本号，返回新版本。"""
    await redis.hset(CONFIG_KEY, _DATA_FIELD, json.dumps(config.to_dict()))
    return await redis.incr(CONFIG_VERSION_KEY)


async def load_locks(redis: aioredis.Redis) -> list[ActiveLock]:
    """读取持久化的活跃锁（用于重启恢复）。"""
    try:
        raw = await redis.hgetall(LOCKS_KEY)
    except Exception:
        logger.exception("Failed to read protection locks from Redis")
        return []
    out: list[ActiveLock] = []
    for value in raw.values():
        try:
            out.append(ActiveLock.from_dict(json.loads(value)))
        except Exception:
            logger.warning("Skipping corrupt persisted lock")
    return out


async def save_lock(redis: aioredis.Redis, lock: ActiveLock) -> None:
    await redis.hset(LOCKS_KEY, lock.id, json.dumps(lock.to_dict()))


async def delete_lock(redis: aioredis.Redis, lock_id: str) -> None:
    await redis.hdel(LOCKS_KEY, lock_id)


async def read_version(redis: aioredis.Redis) -> Optional[int]:
    try:
        v = await redis.get(CONFIG_VERSION_KEY)
        return int(v) if v is not None else None
    except Exception:
        return None
