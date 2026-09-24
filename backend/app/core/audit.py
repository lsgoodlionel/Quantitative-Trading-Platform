"""
审计留痕 — 双写 Redis stream（快速查询层）+ Postgres（持久层）

记录下单、撤单、券商配置变更、风控规则修改、用户增删改、对账等关键操作。
每条记录含时间戳 / 操作者 / 动作 / 详情，倒序可查（见 `endpoints/audit.py`）。

## 存储：双写（V3 · J3）

* **Redis stream `audit:log`** — 快速查询层。maxlen 10000 近似裁剪，
  列表接口的倒序扫描靠它，延迟稳定有界。**会被裁掉、重启可能丢。**
* **Postgres `audit_log` 表** — 持久层，无容量上限（`app/data/storage/audit_log.py`）。

两条路径**互相独立**：一条挂了另一条照写，绝不因为其中一条失败就跳过另一条。

## 失败处理：记 ERROR + 计数，但不阻断主流程

这是一个明确的取舍，两头都不能少：

1. **不静默。** 此前写失败只 `logger.debug` 后继续 —— 审计的意义就是「出事后能查」，
   静默丢失等于没做，而且丢得越久越没人发现。现在一律 `logger.error`，
   并累加 `audit_failure_count()`（可接监控告警）。
2. **不阻断。** 审计写失败**不向调用方抛异常**：审计挂了不该让下单失败。
   下单是用户的钱，审计是我们的账本 —— 账本记不上是我们的事故，
   但不能因此把用户的交易也一起废掉。

因此调用方永远拿不到异常，但运维一定看得到 ERROR 与计数。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Redis stream 键
AUDIT_STREAM = "audit:log"
# stream 最大长度（近似裁剪，保留最近 N 条）
AUDIT_MAXLEN = 10_000

# 写入失败计数（进程内累计，供监控/测试断言）。
# 三个桶都代表「该操作未留痕」，`audit_failure_count()` 默认求和。
_FAILURE_COUNTS: dict[str, int] = {"redis": 0, "postgres": 0, "postgres_skipped": 0}

# Postgres 熔断：连续失败 N 次后停写 M 秒。
#
# 没有它的话，一个没起 Postgres 的部署会给**每一次下单**的热路径都加上一次
# 必然失败的连接尝试（本机实测约 50ms），并且每次都刷一条 ERROR。
# 熔断期间计数照涨（进 `postgres_skipped`），所以监控仍然看得到「审计没落库」，
# 只是不再刷日志、不再拖慢下单。
_PG_BREAKER_THRESHOLD = 3
_PG_BREAKER_COOLDOWN_S = 60.0
_pg_consecutive_failures = 0
_pg_retry_after = 0.0


class AuditAction:
    """审计动作常量（与前端标签映射一一对应）。"""

    ORDER_SUBMIT = "order.submit"
    ORDER_CANCEL = "order.cancel"
    BROKER_CONFIG_SAVE = "broker_config.save"
    BROKER_CONFIG_DELETE = "broker_config.delete"
    RISK_CONFIG_UPDATE = "risk_config.update"
    # V3 Wave B-a：LLM 网关配置（详情只记掩码，绝不记录完整 key）
    LLM_CONFIG_SAVE = "llm_config.save"
    LLM_CONFIG_DELETE = "llm_config.delete"
    LLM_ACTIVE_SWITCH = "llm_config.active_switch"
    # V3 Wave C-b / J3：用户管理（详情只记 user_id/username/role，绝不记密码或其哈希）
    USER_CREATE = "user.create"
    USER_UPDATE = "user.update"
    USER_DELETE = "user.delete"
    # V3 Wave C-b / G6：实盘对账（只读操作，但需要留痕谁在什么时候对过账）
    RECONCILE_RUN = "reconcile.run"


def audit_failure_count(backend: str | None = None) -> int:
    """
    审计写入失败的累计次数（进程内）。

    `backend` 为 None 时返回两个后端之和。监控可定期采样此值；
    非零意味着**有操作没被留痕**，需要人查。
    """
    if backend is None:
        return sum(_FAILURE_COUNTS.values())
    return _FAILURE_COUNTS.get(backend, 0)


def reset_audit_failure_count() -> None:
    """清零失败计数并合上熔断（仅测试与监控采样后使用）。"""
    global _pg_consecutive_failures, _pg_retry_after
    for key in _FAILURE_COUNTS:
        _FAILURE_COUNTS[key] = 0
    _pg_consecutive_failures = 0
    _pg_retry_after = 0.0


def _record_failure(backend: str, action: str, exc: BaseException) -> None:
    """
    统一的失败记账：ERROR 级日志 + 计数。

    用 `logger.error` 而不是 `logger.exception`：审计失败通常是连接层问题，
    完整栈对定位没有额外信息，却会在批量失败时把日志刷爆。
    """
    _FAILURE_COUNTS[backend] = _FAILURE_COUNTS.get(backend, 0) + 1
    logger.error(
        "审计写入失败（%s）· action=%s · %s：%s —— 该操作未留痕，主流程继续",
        backend, action, type(exc).__name__, exc,
    )


async def audit_log(
    action: str,
    actor: str,
    detail: dict[str, Any] | None = None,
    *,
    redis: Any = None,
    session: Any = None,
) -> None:
    """
    双写一条审计记录：Redis stream（快速查询层）+ Postgres（持久层）。

    Args:
        action:  `AuditAction` 中的常量
        actor:   操作者（邮箱 / 用户 id），空则记 "system"
        detail:  结构化详情。**绝不要放密码、完整 API key、token**
        redis:   显式 Redis 客户端；不传则从全局连接池临时借一个并在写后关闭
        session: 显式 AsyncSession；不传则从 `AsyncSessionLocal` 临时开一个

    两条写入路径彼此独立，任一失败只记 ERROR + 计数，**绝不向调用方抛出**
    （理由见模块 docstring）。
    """
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "action": action,
        "actor": actor or "system",
        "detail": json.dumps(detail or {}, ensure_ascii=False, default=str),
    }
    await _write_redis(entry, redis)
    await _write_postgres(action, actor, detail, session)


async def _write_redis(entry: dict[str, str], redis: Any) -> None:
    """写 Redis stream。拿不到客户端也算失败 —— 「没配 Redis」不该让审计静默消失。"""
    action = entry["action"]
    client, created = await _resolve_redis(redis)
    if client is None:
        _record_failure("redis", action, RuntimeError("Redis 客户端不可用"))
        return
    try:
        await client.xadd(AUDIT_STREAM, entry, maxlen=AUDIT_MAXLEN, approximate=True)
    except Exception as exc:
        _record_failure("redis", action, exc)
    finally:
        if created:
            try:
                await client.aclose()
            except Exception:
                # 关连接失败与「审计有没有写成功」无关，不计入失败计数
                logger.debug("审计 Redis 客户端关闭失败", exc_info=True)


async def _write_postgres(
    action: str, actor: str, detail: dict[str, Any] | None, session: Any
) -> None:
    """
    写 Postgres 持久层。整段惰性导入，避免 core → data.storage 的启动期依赖。

    显式传入 session 时**绕过熔断**：调用方已经握着一个可用连接，没有连接成本。
    """
    if session is None and _pg_breaker_open():
        _FAILURE_COUNTS["postgres_skipped"] += 1
        return
    try:
        from app.data.storage.audit_log import PostgresAuditLogStore, build_entry

        entry = build_entry(action, actor, detail)
        if session is not None:
            await PostgresAuditLogStore(session).append(entry)
            return

        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as own_session:
            await PostgresAuditLogStore(own_session).append(entry)
        _pg_breaker_record_success()
    except Exception as exc:
        _record_failure("postgres", action, exc)
        _pg_breaker_record_failure()


def _pg_breaker_open() -> bool:
    """熔断是否处于打开状态（打开 = 本次跳过 Postgres 写入）。"""
    global _pg_consecutive_failures, _pg_retry_after
    if _pg_retry_after == 0.0:
        return False
    if time.monotonic() < _pg_retry_after:
        return True
    # 冷却结束：放行一次探测，成功则彻底合上
    _pg_retry_after = 0.0
    _pg_consecutive_failures = 0
    logger.info("审计 Postgres 熔断冷却结束，恢复尝试写入")
    return False


def _pg_breaker_record_failure() -> None:
    global _pg_consecutive_failures, _pg_retry_after
    _pg_consecutive_failures += 1
    if _pg_consecutive_failures >= _PG_BREAKER_THRESHOLD and _pg_retry_after == 0.0:
        _pg_retry_after = time.monotonic() + _PG_BREAKER_COOLDOWN_S
        logger.warning(
            "审计 Postgres 连续失败 %d 次，暂停写入 %.0f 秒 —— "
            "期间的审计记录不会落库（失败计数继续累加，请查 audit_failure_count）",
            _pg_consecutive_failures, _PG_BREAKER_COOLDOWN_S,
        )


def _pg_breaker_record_success() -> None:
    global _pg_consecutive_failures, _pg_retry_after
    _pg_consecutive_failures = 0
    _pg_retry_after = 0.0


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
