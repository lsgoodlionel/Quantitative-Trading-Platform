"""
审计留痕 — 关键操作写入 Redis stream（audit:log）

记录下单、撤单、券商配置变更、风控规则修改等关键操作。
每条记录含时间戳 / 操作者 / 动作 / 详情，倒序可查（见 endpoints/audit.py）。

存储与 orders:events 一致：Redis stream + maxlen 近似裁剪；
fire-and-forget，绝不因审计写入失败阻断业务热路径。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Redis stream 键
AUDIT_STREAM = "audit:log"
# stream 最大长度（近似裁剪，保留最近 N 条）
AUDIT_MAXLEN = 10_000


class AuditAction:
    """审计动作常量（与前端标签映射一一对应）。"""

    ORDER_SUBMIT = "order.submit"
    ORDER_CANCEL = "order.cancel"
    BROKER_CONFIG_SAVE = "broker_config.save"
    BROKER_CONFIG_DELETE = "broker_config.delete"
    RISK_CONFIG_UPDATE = "risk_config.update"


async def audit_log(
    action: str,
    actor: str,
    detail: dict[str, Any] | None = None,
    *,
    redis: Any = None,
) -> None:
    """
    写入一条审计记录到 Redis stream。

    fire-and-forget：任何异常均被吞掉并记录 debug 日志，绝不向调用方抛出。
    未显式传入 redis 时，从全局连接池临时借用一个客户端并在写入后关闭。
    """
    client, created = await _resolve_redis(redis)
    if client is None:
        logger.debug("audit_log skipped (no redis available): %s", action)
        return

    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "actor": actor or "system",
            "detail": json.dumps(detail or {}, ensure_ascii=False, default=str),
        }
        await client.xadd(
            AUDIT_STREAM, entry, maxlen=AUDIT_MAXLEN, approximate=True
        )
    except Exception:
        logger.debug("Failed to write audit log: %s", action)
    finally:
        if created:
            try:
                await client.aclose()
            except Exception:
                pass


def parse_entry(entry_id: str, fields: dict[str, str]) -> dict[str, Any]:
    """将 Redis stream 原始条目解析为标准审计记录字典。"""
    raw_detail = fields.get("detail", "{}")
    try:
        detail = json.loads(raw_detail)
    except (ValueError, TypeError):
        detail = {"raw": raw_detail}

    return {
        "id": entry_id,
        "ts": fields.get("ts", ""),
        "action": fields.get("action", ""),
        "actor": fields.get("actor", "system"),
        "detail": detail if isinstance(detail, dict) else {"value": detail},
    }


async def _resolve_redis(redis: Any) -> tuple[Any, bool]:
    """返回 (client, created)；created=True 表示需由调用方负责关闭。"""
    if redis is not None:
        return redis, False
    try:
        import redis.asyncio as aioredis

        from app.core.redis import get_redis_pool

        return aioredis.Redis(connection_pool=get_redis_pool()), True
    except Exception:
        return None, False
