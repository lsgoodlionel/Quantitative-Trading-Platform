"""
TimescaleDB 行情存储仓储

负责 bars / ticks 表的读写。
时序查询参考 refs/qlib/qlib/data/ 的数据存储设计。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.data.models import Bar, Frequency, Market, Tick

logger = get_logger(__name__)

# SQL 批量 upsert（基于 TimescaleDB 推荐的 ON CONFLICT DO UPDATE 方式）
_UPSERT_BARS_SQL = text("""
    INSERT INTO bars (time, symbol, market, frequency, open, high, low, close, volume, turnover, vwap)
    VALUES (:time, :symbol, :market, :frequency, :open, :high, :low, :close, :volume, :turnover, :vwap)
    ON CONFLICT (time, symbol, market, frequency) DO UPDATE SET
        open     = EXCLUDED.open,
        high     = EXCLUDED.high,
        low      = EXCLUDED.low,
        close    = EXCLUDED.close,
        volume   = EXCLUDED.volume,
        turnover = EXCLUDED.turnover,
        vwap     = EXCLUDED.vwap
""")

_SELECT_BARS_SQL = text("""
    SELECT time, symbol, market, frequency, open, high, low, close, volume, turnover, vwap
    FROM bars
    WHERE symbol   = :symbol
      AND market   = :market
      AND frequency = :frequency
      AND time >= :start
      AND time <= :end
    ORDER BY time ASC
    LIMIT :limit
""")

_SELECT_LATEST_BAR_SQL = text("""
    SELECT time, symbol, market, frequency, open, high, low, close, volume, turnover, vwap
    FROM bars
    WHERE symbol    = :symbol
      AND market    = :market
      AND frequency = :frequency
    ORDER BY time DESC
    LIMIT 1
""")

_UPSERT_TICK_SQL = text("""
    INSERT INTO ticks (time, symbol, market, bid_price, ask_price, bid_size, ask_size, last_price, last_size)
    VALUES (:time, :symbol, :market, :bid_price, :ask_price, :bid_size, :ask_size, :last_price, :last_size)
    ON CONFLICT (time, symbol, market) DO UPDATE SET
        bid_price  = EXCLUDED.bid_price,
        ask_price  = EXCLUDED.ask_price,
        last_price = EXCLUDED.last_price,
        last_size  = EXCLUDED.last_size
""")


def _row_to_bar(row: Any, symbol: str, market: Market, frequency: Frequency) -> Bar:
    ts: datetime = row.time
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return Bar(
        time=ts,
        symbol=symbol,
        market=market,
        frequency=frequency,
        open=float(row.open),
        high=float(row.high),
        low=float(row.low),
        close=float(row.close),
        volume=int(row.volume),
        vwap=float(row.vwap) if row.vwap is not None else None,
        turnover=float(row.turnover) if row.turnover is not None else None,
    )


class TimeseriesRepository:
    """TimescaleDB 行情数据仓储。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_bars(self, bars: list[Bar]) -> int:
        """批量 upsert K 线数据，返回写入条数。"""
        if not bars:
            return 0

        params = [
            {
                "time": b.time,
                "symbol": b.symbol,
                "market": b.market.value,
                "frequency": b.frequency.value,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "turnover": b.turnover,
                "vwap": b.vwap,
            }
            for b in bars
        ]

        # 分批写入，避免单次参数过多
        BATCH_SIZE = 500
        total = 0
        for i in range(0, len(params), BATCH_SIZE):
            batch = params[i : i + BATCH_SIZE]
            await self._session.execute(_UPSERT_BARS_SQL, batch)
            total += len(batch)

        logger.debug("Saved bars to TimescaleDB", count=total, symbol=bars[0].symbol)
        return total

    async def get_bars(
        self,
        symbol: str,
        market: Market,
        frequency: Frequency,
        start: date,
        end: date,
        limit: int = 5000,
    ) -> list[Bar]:
        """从 TimescaleDB 查询历史 K 线。"""
        result = await self._session.execute(
            _SELECT_BARS_SQL,
            {
                "symbol": symbol,
                "market": market.value,
                "frequency": frequency.value,
                "start": datetime.combine(start, datetime.min.time()).replace(tzinfo=timezone.utc),
                "end": datetime.combine(end, datetime.max.time()).replace(tzinfo=timezone.utc),
                "limit": limit,
            },
        )
        return [_row_to_bar(row, symbol, market, frequency) for row in result.fetchall()]

    async def get_latest_bar(
        self,
        symbol: str,
        market: Market,
        frequency: Frequency,
    ) -> Bar | None:
        """获取 TimescaleDB 中最新一根 K 线。"""
        result = await self._session.execute(
            _SELECT_LATEST_BAR_SQL,
            {"symbol": symbol, "market": market.value, "frequency": frequency.value},
        )
        row = result.fetchone()
        return _row_to_bar(row, symbol, market, frequency) if row else None

    async def save_tick(self, tick: Tick) -> None:
        await self._session.execute(
            _UPSERT_TICK_SQL,
            {
                "time": tick.time,
                "symbol": tick.symbol,
                "market": tick.market.value,
                "bid_price": tick.bid_price,
                "ask_price": tick.ask_price,
                "bid_size": tick.bid_size,
                "ask_size": tick.ask_size,
                "last_price": tick.last_price,
                "last_size": tick.last_size,
            },
        )

    async def count_bars(
        self,
        symbol: str,
        market: Market,
        frequency: Frequency,
    ) -> int:
        """统计某标的的 K 线数量（用于数据质量检查）。"""
        result = await self._session.execute(
            text("""
                SELECT COUNT(*) FROM bars
                WHERE symbol = :symbol AND market = :market AND frequency = :frequency
            """),
            {"symbol": symbol, "market": market.value, "frequency": frequency.value},
        )
        row = result.fetchone()
        return int(row[0]) if row else 0
