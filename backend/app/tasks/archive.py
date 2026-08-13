"""
归档下载任务（M1）

把一批标的的历史 bar 拉下来写进本地归档，供回测反复调参时秒级取数。

沿用 `app/tasks/data.py` 已有的「自建 async engine + `asyncio.run()` 桥接」约定：
Celery worker 进程里没有 FastAPI 的 request context，也就没有依赖注入的
数据库 session，所以每个任务自己建 engine、用完 dispose。**不新建基础设施。**

⚠️ 本任务**绕过** `settings.archive_enabled` 直接写归档 —— 那个开关管的是
「回测取数要不要读归档」，而这里是用户显式点了「下载」。两者语义不同：
不能因为读路径关着就让显式下载静默不生效。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from celery import shared_task

logger = logging.getLogger(__name__)

MAX_SYMBOLS_PER_TASK = 200


@shared_task(
    name="app.tasks.archive.download_archive",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def download_archive(
    self,
    symbols: list[str],
    market: str,
    frequency: str = "1d",
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """
    批量下载并归档历史 bar。

    Args:
        symbols:   标的代码列表（最多 200 个）
        market:    "US" / "HK" / "A"
        frequency: K 线周期
        start/end: ISO 日期字符串（闭区间）

    Returns:
        {"market", "frequency", "requested", "archived", "written", "errors", "failed"}
    """
    targets = list(symbols or [])[:MAX_SYMBOLS_PER_TASK]
    if not targets:
        return _empty_result(market, frequency)

    try:
        start_date, end_date = _parse_window(start, end)
    except ValueError as exc:
        logger.error("download_archive 参数非法: %s", exc)
        return {**_empty_result(market, frequency), "errors": 1, "failed": [str(exc)]}

    written = 0
    archived = 0
    failed: list[str] = []

    for symbol in targets:
        try:
            count = asyncio.run(
                _archive_one(symbol, market, frequency, start_date, end_date)
            )
            written += count
            archived += 1
        except Exception as exc:  # noqa: BLE001, PERF203
            logger.warning("归档下载失败 %s/%s: %s", market, symbol, exc)
            failed.append(f"{symbol}: {exc}")

    return {
        "market": market,
        "frequency": frequency,
        "requested": len(targets),
        "archived": archived,
        "written": written,
        "errors": len(failed),
        "failed": failed[:20],
    }


def _empty_result(market: str, frequency: str) -> dict:
    return {
        "market": market, "frequency": frequency, "requested": 0,
        "archived": 0, "written": 0, "errors": 0, "failed": [],
    }


def _parse_window(start: str | None, end: str | None) -> tuple[date, date]:
    """解析下载区间；默认最近一年。"""
    from datetime import timedelta

    end_date = date.fromisoformat(end) if end else date.today()
    start_date = date.fromisoformat(start) if start else end_date - timedelta(days=365)
    if end_date < start_date:
        raise ValueError(f"end {end_date} 早于 start {start_date}")
    return start_date, end_date


async def _archive_one(
    symbol: str, market: str, frequency: str, start: date, end: date
) -> int:
    """拉单个标的并写归档，返回实际新增的 bar 数。"""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.data.archive import ArchiveKey, create_archive
    from app.data.models import Frequency, Market
    from app.data.service import DataService

    market_enum = Market(market.upper())
    freq_enum = Frequency(frequency)
    key = ArchiveKey(symbol=symbol, market=market_enum, frequency=freq_enum)

    engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            svc = DataService(session)
            bars, is_synthetic = await svc.fetch_direct(
                symbol=symbol, market=market_enum, frequency=freq_enum,
                start=start, end=end, use_cache=False,
            )
    finally:
        await engine.dispose()

    if is_synthetic:
        # 合成数据落盘就再也分辨不出来了，显式报错让用户知道这次没拿到真数据
        raise RuntimeError(f"{symbol} 全部数据源失败，仅拿到合成数据，拒绝写入归档")
    if not bars:
        return 0
    return create_archive().write(key, bars)
