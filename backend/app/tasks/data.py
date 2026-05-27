"""
数据任务模块

包含:
1. backfill_market   — 按市场批量回填历史日线数据
2. backfill_symbol   — 单标的回填任务（支持任意周期）
3. cleanup_cache     — 清理 Redis 过期缓存
4. warm_symbol_cache — 预热指定标的的数据缓存

所有任务均为异步 Celery 任务，通过 asyncio.run() 桥接同步 Celery worker。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Optional

from celery import shared_task

logger = logging.getLogger(__name__)

# 各市场热门标的（首次回填时使用）
_WATCHLIST: dict[str, list[str]] = {
    "US": [
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN",
        "META", "TSLA", "SPY", "QQQ", "BRK.B",
    ],
    "HK": [
        "00700", "09988", "03690", "01299", "02318",
        "00941", "01810", "02020", "00388", "01398",
    ],
    "A": [
        "000001", "000002", "600519", "300750", "600036",
        "601318", "000858", "002594", "300760", "600276",
    ],
}


@shared_task(
    name="app.tasks.data.backfill_market",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def backfill_market(
    self,
    market: str,
    frequency: str = "1d",
    days: int = 365,
) -> dict:
    """
    按市场批量回填历史数据。

    Args:
        market:    "US" / "HK" / "A"
        frequency: K 线周期，如 "1d" / "1h"
        days:      回填天数

    Returns:
        {"market": ..., "symbols": ..., "total_bars": ..., "errors": ...}
    """
    symbols = _WATCHLIST.get(market.upper(), [])
    if not symbols:
        return {"market": market, "symbols": 0, "total_bars": 0, "errors": 0}

    logger.info("Starting backfill: market=%s freq=%s days=%d symbols=%d",
                market, frequency, days, len(symbols))

    total_bars = 0
    errors = 0

    for symbol in symbols:
        try:
            count = asyncio.run(_backfill_one(symbol, market, frequency, days))
            total_bars += count
            logger.debug("Backfilled %s: %d bars", symbol, count)
        except Exception as e:
            errors += 1
            logger.warning("Backfill failed for %s: %s", symbol, e)

    result = {
        "market": market,
        "frequency": frequency,
        "symbols": len(symbols),
        "total_bars": total_bars,
        "errors": errors,
        "date": date.today().isoformat(),
    }
    logger.info("Backfill complete: %s", result)
    return result


@shared_task(
    name="app.tasks.data.backfill_symbol",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def backfill_symbol(
    self,
    symbol: str,
    market: str,
    frequency: str = "1d",
    days: int = 365,
) -> dict:
    """
    单标的历史数据回填任务。

    Args:
        symbol:    标的代码
        market:    市场
        frequency: K 线周期
        days:      回填天数

    Returns:
        {"symbol": ..., "bars": ..., "status": "ok" | "error"}
    """
    try:
        count = asyncio.run(_backfill_one(symbol, market, frequency, days))
        return {"symbol": symbol, "market": market, "bars": count, "status": "ok"}
    except Exception as e:
        logger.error("backfill_symbol failed: %s %s — %s", symbol, market, e)
        raise self.retry(exc=e)


@shared_task(name="app.tasks.data.cleanup_cache")
def cleanup_cache() -> dict:
    """
    清理过期 Redis 缓存键。

    当前实现：
    - 检查 Redis 连通性
    - 将来可扩展为 LRU 淘汰或过期键扫描
    """
    try:
        import redis
        from app.core.config import settings
        r = redis.from_url(settings.redis_url, socket_connect_timeout=5)
        r.ping()
        info = r.info("memory")
        used_mb = info.get("used_memory", 0) / 1024 / 1024
        logger.debug("Redis memory usage: %.1f MB", used_mb)
        return {"status": "ok", "redis_memory_mb": round(used_mb, 2)}
    except Exception as e:
        logger.warning("cleanup_cache: Redis check failed — %s", e)
        return {"status": "error", "error": str(e)}


@shared_task(
    name="app.tasks.data.warm_symbol_cache",
    bind=True,
    max_retries=2,
)
def warm_symbol_cache(
    self,
    symbol: str,
    market: str,
    frequency: str = "1d",
) -> dict:
    """
    预热单标的数据缓存（用户首次访问某标的时触发）。

    回填 2 年日线 + 1 年小时线（如果支持）。
    """
    results: dict = {}
    for freq, days in [("1d", 730), ("1h", 90)]:
        try:
            count = asyncio.run(_backfill_one(symbol, market, freq, days))
            results[freq] = count
        except Exception as e:
            logger.warning("warm_cache %s %s %s: %s", symbol, market, freq, e)
            results[freq] = 0

    return {"symbol": symbol, "market": market, "bars": results, "status": "ok"}


# ── 私有异步辅助 ──────────────────────────────────────────────

async def _backfill_one(
    symbol: str,
    market: str,
    frequency: str,
    days: int,
) -> int:
    """
    异步回填单标的数据，返回写入的 bar 数量。

    使用独立的数据库 session（Celery worker 进程中没有 FastAPI request context）。
    """
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
    from app.core.config import settings
    from app.data.models import Market, Frequency
    from app.data.service import DataService

    engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    end = date.today()
    start = end - timedelta(days=days)

    async with factory() as session:
        svc = DataService(session)
        try:
            market_enum = Market(market.upper())
            freq_enum = Frequency(frequency)
        except ValueError:
            return 0

        bars = await svc.get_bars(
            symbol=symbol,
            market=market_enum,
            frequency=freq_enum,
            start=start,
            end=end,
            use_cache=False,  # 强制从数据源拉取
        )

    await engine.dispose()
    return len(bars)
