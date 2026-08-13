"""
宇宙数据的 Redis 缓存（M7）

逐个标的拉市值在 500 只规模下会非常慢且触发限流，所以榜单结果整体缓存。
Redis 不可用时静默降级为「不缓存」——与 screener/pairlist 的既有口径一致，
缓存不可用不该让功能整体不可用。
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 市值榜单缓存 6 小时：市值排名日内几乎不变，而全量拉取代价很高
MARKET_CAP_TTL = 6 * 3600
# 成分股缓存 12 小时：指数调仓是季度级事件
COMPONENTS_TTL = 12 * 3600


def _client():
    """同步 Redis 客户端；不可用返回 None。"""
    try:
        import redis as sync_redis

        from app.core.config import settings
        return sync_redis.from_url(settings.redis_url, decode_responses=True)
    except Exception as exc:  # noqa: BLE001
        logger.debug("universe redis unavailable: %s", exc)
        return None


def read_json(key: str) -> Any | None:
    """读缓存；未命中或异常返回 None。"""
    client = _client()
    if client is None:
        return None
    try:
        raw = client.get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("universe cache read failed (%s): %s", key, exc)
        return None
    finally:
        _close(client)


def write_json(key: str, value: Any, ttl: int) -> None:
    """写缓存；失败只记日志，不影响调用方。"""
    client = _client()
    if client is None:
        return
    try:
        client.setex(key, ttl, json.dumps(value))
    except Exception as exc:  # noqa: BLE001
        logger.debug("universe cache write failed (%s): %s", key, exc)
    finally:
        _close(client)


def _close(client) -> None:
    """连接必须在异常路径上也关掉，否则每次缓存失败都漏一个 socket。"""
    try:
        client.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("universe redis close failed: %s", exc)
