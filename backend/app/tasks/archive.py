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

from app.notify.emit import emit_data_gap

logger = logging.getLogger(__name__)

MAX_SYMBOLS_PER_TASK = 200

# 缺口容忍天数（Wave O-a / O4）。
#
# 归档里本来就没有非交易日的数据，所以「请求到周日、归档只到周五」这种两天尾巴
# 是正常现象。不设容忍度的话每次下载都会报缺口，用户学会忽略它之后，真出缺口
# 那次也不会有人看。5 天足以盖住周末与常见连假。
MIN_GAP_DAYS = 5

# 结果里回显的缺口/失败明细条数上限，避免几百个标的撑爆返回体
MAX_LISTED_ISSUES = 20


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
        {"market", "frequency", "requested", "archived", "written", "errors",
         "failed", "gaps"}
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
    succeeded: list[str] = []
    failed: list[str] = []

    for symbol in targets:
        try:
            count = asyncio.run(
                _archive_one(symbol, market, frequency, start_date, end_date)
            )
            written += count
            succeeded.append(symbol)
        except Exception as exc:  # noqa: BLE001, PERF203
            logger.warning("归档下载失败 %s/%s: %s", market, symbol, exc)
            failed.append(f"{symbol}: {exc}")

    gaps = _notify_gaps(succeeded, market, frequency, start_date, end_date, failed)

    return {
        "market": market,
        "frequency": frequency,
        "requested": len(targets),
        "archived": len(succeeded),
        "written": written,
        "errors": len(failed),
        "failed": failed[:MAX_LISTED_ISSUES],
        "gaps": gaps[:MAX_LISTED_ISSUES],
    }


def _empty_result(market: str, frequency: str) -> dict:
    return {
        "market": market, "frequency": frequency, "requested": 0,
        "archived": 0, "written": 0, "errors": 0, "failed": [], "gaps": [],
    }


def _notify_gaps(
    succeeded: list[str],
    market: str,
    frequency: str,
    start: date,
    end: date,
    failed: list[str],
) -> list[str]:
    """
    检测缺口并发 `data_gap` 通知（Wave O-a / O4）。

    整段包在 try 里：缺口检测与通知都是旁路，坏掉最多让这次下载少一条信息，
    绝不能把已经下好的数据变成一次「失败的下载」。
    """
    try:
        gaps = _collect_gaps(succeeded, market, frequency, start, end)
        emit_data_gap(market=market, frequency=frequency, gaps=gaps, failures=failed)
        return gaps
    except Exception:
        logger.exception("缺口检测/通知失败，不影响下载结果 · market=%s", market)
        return []


def _collect_gaps(
    symbols: list[str], market: str, frequency: str, start: date, end: date
) -> list[str]:
    """扫描已归档标的两端仍未覆盖的区间，短于 `MIN_GAP_DAYS` 的尾巴不算缺口。"""
    from app.data.archive import ArchiveKey, boundary_gaps, create_archive
    from app.data.models import Frequency, Market

    if not symbols:
        return []

    archive = create_archive()
    market_enum = Market(market.upper())
    freq_enum = Frequency(frequency)

    described: list[str] = []
    for symbol in symbols:
        key = ArchiveKey(symbol=symbol, market=market_enum, frequency=freq_enum)
        gaps = [
            gap for gap in boundary_gaps(archive.coverage(key), start, end)
            if gap.days >= MIN_GAP_DAYS
        ]
        if gaps:
            described.append(
                f"{symbol}: " + ", ".join(f"{g.start}~{g.end}" for g in gaps)
            )
    return described


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
