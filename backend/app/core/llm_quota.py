"""LLM 调用的按用户日配额。

**为什么是配额而不是 RBAC。** 「Copilot 花钱，是不是该抬到 TRADER？」——
不该。角色是权限的刻度，不是花费的刻度：admin 一样能把账单打爆，而把助手
挡在 TRADER 之后，恰好拦住了最需要它的那批人（只读用户来问「这个指标怎么看」
是这个功能存在的理由）。对得上「花钱」这个顾虑的控制项是**用量上限**。

所以四个会掏钱的入口一律保持 `Role.VIEWER`，改由这里限量：

- `POST /copilot/chat`
- `POST /ai/reports/stock`
- `POST /ai/reports/backtest`
- 自动因子循环的 LLM 复盘/提种子

**失败时放行（fail-open），但记 ERROR。** Redis 挂了有两种选择：
放行 = 计数器停摆期间不限量；拒绝 = 计数器一挂助手就死。
这是自托管的小团队平台，把一个附属计数器做成全站 AI 功能的单点，
代价远大于它防的那点花费。放行并记 ERROR，让监控看得见。

**日窗口按 UTC 自然日切**，不做滑动窗口：滑动窗口要存时间戳列表，
而这里要的只是「别跑飞」，不是精确计费。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.rbac import Role, normalize_role

logger = logging.getLogger(__name__)

#: 各角色的每日 LLM 调用上限。viewer 给得少但不是不给。
DAILY_LIMITS: dict[Role, int] = {
    Role.VIEWER: 50,
    Role.TRADER: 200,
    Role.ADMIN: 500,
}

#: 计数键的过期时间：两天，跨过 UTC 日切后自然回收
_KEY_TTL_SECONDS = 2 * 24 * 3600

_KEY_PREFIX = "llm:quota"


@dataclass(frozen=True)
class QuotaState:
    """一次配额检查的结果。"""

    allowed: bool
    used: int
    limit: int
    #: True = Redis 不可用，本次是放行而非「确实没超」。见模块文档。
    degraded: bool = False

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "used": self.used,
            "limit": self.limit,
            "remaining": self.remaining,
            "degraded": self.degraded,
        }


def limit_for(role: str | Role | None) -> int:
    """该角色的日上限。未知角色按 viewer 处理（fail-safe，绝不提额）。"""
    return DAILY_LIMITS[normalize_role(role if isinstance(role, str) else
                                       (role.value if role else None))]


def quota_key(user_id: str, *, today: str | None = None) -> str:
    return f"{_KEY_PREFIX}:{today or _utc_day()}:{user_id}"


async def consume(
    user_id: str, role: str | Role | None, *, redis: Any = None
) -> QuotaState:
    """记一次 LLM 调用并返回是否放行。

    先自增再判断：这样并发请求不会因为「都读到 used=limit-1」而一起放行。
    超限时把刚加上的那一次减回去 —— 否则被拒的请求也会推高计数，
    用户会看到「已用 73/50」这种读不通的数字。
    """
    limit = limit_for(role)
    client, created = await _resolve_redis(redis)
    if client is None:
        logger.error("LLM 配额计数不可用（Redis 未就绪），本次放行 user_id=%s", user_id)
        return QuotaState(allowed=True, used=0, limit=limit, degraded=True)

    key = quota_key(user_id)
    try:
        used = int(await client.incr(key))
        if used == 1:
            await client.expire(key, _KEY_TTL_SECONDS)
        if used > limit:
            await client.decr(key)
            return QuotaState(allowed=False, used=limit, limit=limit)
        return QuotaState(allowed=True, used=used, limit=limit)
    except Exception as exc:  # noqa: BLE001 —— 见模块文档：放行但记 ERROR
        logger.error("LLM 配额计数失败，本次放行 user_id=%s：%s", user_id, exc)
        return QuotaState(allowed=True, used=0, limit=limit, degraded=True)
    finally:
        if created:
            await _safe_close(client)


async def peek(
    user_id: str, role: str | Role | None, *, redis: Any = None
) -> QuotaState:
    """只读地看一眼用量，**不计数**。给「还剩多少次」这类展示用。"""
    limit = limit_for(role)
    client, created = await _resolve_redis(redis)
    if client is None:
        return QuotaState(allowed=True, used=0, limit=limit, degraded=True)
    try:
        raw = await client.get(quota_key(user_id))
        used = int(raw) if raw else 0
        return QuotaState(allowed=used < limit, used=used, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.error("LLM 配额查询失败 user_id=%s：%s", user_id, exc)
        return QuotaState(allowed=True, used=0, limit=limit, degraded=True)
    finally:
        if created:
            await _safe_close(client)


# ── 内部 ─────────────────────────────────────────────────────────────────────

def _utc_day() -> str:
    return datetime.now(UTC).strftime("%Y%m%d")


async def _resolve_redis(redis: Any) -> tuple[Any, bool]:
    """返回 (client, created)；created=True 表示需由本模块负责关闭。

    与 `app/core/audit.py::_resolve_redis` 同形，刻意不抽公共函数：
    两处的降级语义不同（审计是双写、这里是放行），合并反而会把差异藏起来。
    """
    if redis is not None:
        return redis, False
    try:
        import redis.asyncio as aioredis

        from app.core.redis import get_redis_pool

        return aioredis.Redis(connection_pool=get_redis_pool()), True
    except Exception:  # noqa: BLE001
        return None, False


async def _safe_close(client: Any) -> None:
    try:
        await client.aclose()
    except Exception:  # noqa: BLE001 —— 关连接失败不该盖掉业务结果
        logger.debug("关闭配额 Redis 连接失败", exc_info=True)
