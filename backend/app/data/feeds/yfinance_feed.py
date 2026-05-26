"""
yfinance 备用数据源 — 美股/港股历史数据（免费，无需 API key）

用于:
1. Alpaca key 未配置时的美股历史数据备用
2. 富途 OpenD 未运行时的港股历史数据备用
3. 数据交叉验证

港股代码映射: 00700 → 0700.HK (yfinance 格式)
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import pandas as pd

from app.core.logging import get_logger
from app.data.feeds.base import DataFeed
from app.data.models import Bar, Frequency, Market, SymbolInfo, Tick

logger = get_logger(__name__)

# yfinance interval 参数映射
_FREQ_TO_INTERVAL: dict[Frequency, str] = {
    Frequency.MIN_1: "1m",
    Frequency.MIN_5: "5m",
    Frequency.MIN_15: "15m",
    Frequency.MIN_30: "30m",
    Frequency.HOUR_1: "1h",
    Frequency.DAY_1: "1d",
    Frequency.WEEK_1: "1wk",
}

# yfinance 最大可拉取历史天数（按 interval 限制）
_INTERVAL_MAX_DAYS: dict[str, int] = {
    "1m": 7,
    "5m": 60,
    "15m": 60,
    "30m": 60,
    "1h": 730,
    "1d": 36500,
    "1wk": 36500,
}


def _to_yf_symbol(symbol: str, market: Market) -> str:
    """内部代码 → yfinance 代码格式"""
    if market == Market.HK:
        # 00700 → 0700.HK，去掉前导零后加 .HK
        clean = symbol.lstrip("0") or "0"
        if not clean.endswith(".HK"):
            return f"{clean}.HK"
    return symbol


def _df_to_bars(df: pd.DataFrame, symbol: str, market: Market, frequency: Frequency) -> list[Bar]:
    bars: list[Bar] = []
    for ts, row in df.iterrows():
        if pd.isna(row.get("Close", float("nan"))):
            continue
        if hasattr(ts, "to_pydatetime"):
            dt: datetime = ts.to_pydatetime()
        else:
            dt = datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        bars.append(
            Bar(
                time=dt,
                symbol=symbol,
                market=market,
                frequency=frequency,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=int(row.get("Volume", 0)),
            )
        )
    return bars


class YFinanceDataFeed(DataFeed):
    """
    yfinance 备用数据源。

    美股/港股历史数据，无需 API key，免费但有限制:
    - 分钟级数据最多 60 天
    - 无实时推送
    """

    market = Market.US  # 支持 US 和 HK，market 属性仅供标识

    def __init__(self, market: Market = Market.US) -> None:
        self.market = market

    async def get_bars(
        self,
        symbol: str,
        frequency: Frequency,
        start: date,
        end: date,
    ) -> list[Bar]:
        if frequency not in _FREQ_TO_INTERVAL:
            raise ValueError(f"Unsupported frequency for yfinance: {frequency}")

        interval = _FREQ_TO_INTERVAL[frequency]
        yf_symbol = _to_yf_symbol(symbol, self.market)

        def _fetch() -> pd.DataFrame:
            try:
                import yfinance as yf
            except ImportError as e:
                raise RuntimeError("yfinance not installed: pip install yfinance") from e

            ticker = yf.Ticker(yf_symbol)
            return ticker.history(
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval=interval,
                auto_adjust=True,
            )

        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(None, _fetch)

        if df.empty:
            logger.warning("yfinance returned empty data", symbol=yf_symbol, interval=interval)
            return []

        bars = _df_to_bars(df, symbol, self.market, frequency)
        logger.info(
            "Fetched historical bars",
            feed="yfinance",
            symbol=yf_symbol,
            frequency=frequency.value,
            count=len(bars),
        )
        return bars

    async def get_latest_bar(self, symbol: str, frequency: Frequency) -> Bar | None:
        bars = await self.get_bars(symbol, frequency, date.today(), date.today())
        return bars[-1] if bars else None

    @property
    def supports_realtime(self) -> bool:
        return False

    async def search_symbols(self, query: str) -> list[SymbolInfo]:
        """yfinance 无搜索 API，返回空列表。"""
        return []
