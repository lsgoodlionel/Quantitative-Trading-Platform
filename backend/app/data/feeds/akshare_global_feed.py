"""
AkShare 美股/港股数据源 — 免费日线历史（无需 API key）

复用已安装的 akshare（零新增依赖），为美股/港股提供又一免费历史数据源：
- 美股: ak.stock_us_daily(symbol='AAPL')
- 港股: ak.stock_hk_daily(symbol='00700')

两接口均返回全量日线历史（date/open/high/low/close/volume），本模块按
请求区间过滤。仅日线，无分钟级、无实时推送。作为 US/HK 的免费备用源，
与 Alpaca/Futu/yfinance/Stooq 一起构成多源冗余。
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import pandas as pd

from app.core.logging import get_logger
from app.data.feeds.base import DataFeed
from app.data.models import Bar, Frequency, Market, SymbolInfo, Tick

logger = get_logger(__name__)

_SUPPORTED = {Frequency.DAY_1}


def _df_to_bars(
    df: pd.DataFrame, symbol: str, market: Market, start: date, end: date
) -> list[Bar]:
    bars: list[Bar] = []
    for _, row in df.iterrows():
        close = row.get("close")
        if close is None or pd.isna(close):
            continue
        try:
            d = datetime.fromisoformat(str(row["date"])).date()
        except Exception:
            try:
                d = pd.to_datetime(row["date"]).date()
            except Exception:
                continue
        if d < start or d > end:
            continue
        dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        bars.append(
            Bar(
                time=dt,
                symbol=symbol,
                market=market,
                frequency=Frequency.DAY_1,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(close),
                volume=int(row.get("volume", 0) or 0),
            )
        )
    return bars


class _AkShareGlobalBase(DataFeed):
    """AkShare US/HK 日线基类。子类指定 market 与取数函数。"""

    market = Market.US

    def _fetch_fn(self, symbol: str) -> pd.DataFrame:  # pragma: no cover - 子类实现
        raise NotImplementedError

    def _to_ak_symbol(self, symbol: str) -> str:
        return symbol

    async def get_bars(
        self, symbol: str, frequency: Frequency, start: date, end: date
    ) -> list[Bar]:
        if frequency not in _SUPPORTED:
            raise ValueError(f"AkShare {self.market.value} 仅支持日线，不支持: {frequency}")

        ak_symbol = self._to_ak_symbol(symbol)

        def _fetch() -> pd.DataFrame:
            return self._fetch_fn(ak_symbol)

        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(None, _fetch)
        if not isinstance(df, pd.DataFrame) or df.empty:
            logger.warning("AkShare global returned empty", symbol=ak_symbol, market=self.market.value)
            return []

        bars = _df_to_bars(df, symbol, self.market, start, end)
        logger.info(
            "Fetched historical bars",
            feed=f"akshare_{self.market.value.lower()}",
            symbol=ak_symbol,
            count=len(bars),
        )
        return bars

    async def get_latest_bar(self, symbol: str, frequency: Frequency) -> Bar | None:
        from datetime import timedelta

        end = date.today()
        bars = await self.get_bars(symbol, frequency, end - timedelta(days=15), end)
        return bars[-1] if bars else None

    async def get_latest_tick(self, symbol: str) -> Tick | None:
        return None

    @property
    def supports_realtime(self) -> bool:
        return False

    async def search_symbols(self, query: str) -> list[SymbolInfo]:
        from app.data.symbol_dict import search_by_cn_name

        return [
            SymbolInfo(symbol=sym, name=cn, name_zh=cn, market=m)
            for sym, m, cn in search_by_cn_name(query, self.market)
            if m == self.market
        ]


class AkShareUSFeed(_AkShareGlobalBase):
    """AkShare 美股日线（ak.stock_us_daily）。"""

    market = Market.US

    def _fetch_fn(self, symbol: str) -> pd.DataFrame:
        import akshare as ak

        return ak.stock_us_daily(symbol=symbol.upper())


class AkShareHKFeed(_AkShareGlobalBase):
    """AkShare 港股日线（ak.stock_hk_daily）。港股代码补零到 5 位。"""

    market = Market.HK

    def _to_ak_symbol(self, symbol: str) -> str:
        digits = "".join(c for c in symbol if c.isdigit())
        return f"{int(digits):05d}" if digits else symbol

    def _fetch_fn(self, symbol: str) -> pd.DataFrame:
        import akshare as ak

        return ak.stock_hk_daily(symbol=symbol)
