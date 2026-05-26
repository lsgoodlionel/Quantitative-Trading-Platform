"""
演示数据源 — 生成合成 OHLCV 数据

当所有真实数据源（Alpaca / Futu / yfinance）均不可用时作为最终兜底。
基于几何布朗运动生成逼真的价格序列；同一标的+固定种子保证每次返回一致数据。

适用场景:
  - 本地开发无 API key
  - Docker 环境 yfinance 被 rate-limit
  - CI / 演示部署

注意: 本数据源不支持分钟级数据（只生成日线/周线）。
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone

import numpy as np

from app.core.logging import get_logger
from app.data.feeds.base import DataFeed
from app.data.models import Bar, Frequency, Market, SymbolInfo, Tick

logger = get_logger(__name__)

# 常见标的的参考起始价（用于生成看起来合理的价格序列）
_BASE_PRICES: dict[str, float] = {
    # 美股
    "AAPL": 189.5,   "MSFT": 432.1,   "NVDA": 906.8,   "TSLA": 177.3,
    "GOOG": 175.5,   "GOOGL": 175.5,  "AMZN": 210.2,   "META": 565.3,
    "NFLX": 710.0,   "BRK.B": 440.0,  "JPM": 210.0,    "V": 280.0,
    "WMT": 88.0,     "UNH": 530.0,    "XOM": 115.0,    "LLY": 770.0,
    "SPY": 520.0,    "QQQ": 450.0,    "IWM": 200.0,    "DIA": 400.0,
    # 港股
    "0700": 380.0,   "0700.HK": 380.0, "00700": 380.0,
    "9988": 90.0,    "9988.HK": 90.0,  "09988": 90.0,
    "1299": 65.0,    "1299.HK": 65.0,  "00941": 52.0,
    # A 股
    "600519": 1800.0, "000858": 45.0,  "601318": 44.0,
    "300750": 180.0,  "000002": 9.5,
}

_DEFAULT_PRICE = 100.0
_ANNUAL_MU = 0.12      # 12% 年化漂移
_ANNUAL_SIGMA = 0.22   # 22% 年化波动率
_TRADING_DAYS = 252

_DAILY_MU = _ANNUAL_MU / _TRADING_DAYS
_DAILY_SIGMA = _ANNUAL_SIGMA / (_TRADING_DAYS ** 0.5)

_EPOCH = date(2018, 1, 2)  # 合成数据的起始日期


def _seed_for(symbol: str) -> int:
    """将标的代码映射到固定随机种子（哈希保证稳定性）。"""
    return int(hashlib.sha256(symbol.upper().encode()).hexdigest()[:8], 16) % (2 ** 31)


def _build_price_series(base: float, n: int, seed: int) -> list[float]:
    """用 GBM 生成 n+1 个收盘价序列（含起始价）。"""
    rng = np.random.default_rng(seed)
    prices = [base]
    for _ in range(n):
        ret = rng.normal(_DAILY_MU, _DAILY_SIGMA)
        prices.append(max(prices[-1] * np.exp(ret), 0.01))
    return prices


def _prices_to_bars(
    prices: list[float],
    start: date,
    end: date,
    symbol: str,
    market: Market,
    frequency: Frequency,
    seed: int,
) -> list[Bar]:
    """将价格序列与日期序列对齐，生成 Bar 列表（仅工作日）。"""
    rng = np.random.default_rng(seed + 1)  # 独立种子用于 intraday 扰动
    bars: list[Bar] = []

    current = _EPOCH
    price_idx = 0

    while current <= end and price_idx + 1 < len(prices):
        if current.weekday() < 5:  # 周一到周五
            if current >= start:
                o = prices[price_idx]
                c = prices[price_idx + 1]
                intraday_range = abs(rng.normal(0, _DAILY_SIGMA)) * o
                h = round(max(o, c) + intraday_range * rng.uniform(0.1, 0.5), 4)
                l = round(max(min(o, c) - intraday_range * rng.uniform(0.1, 0.5), 0.01), 4)
                vol = int(rng.integers(200_000, 8_000_000))

                ts = datetime(current.year, current.month, current.day, 16, 0, tzinfo=timezone.utc)
                bars.append(Bar(
                    time=ts,
                    symbol=symbol,
                    market=market,
                    frequency=frequency,
                    open=round(o, 4),
                    high=h,
                    low=l,
                    close=round(c, 4),
                    volume=vol,
                ))
            price_idx += 1
        current += timedelta(days=1)

    return bars


class DemoDataFeed(DataFeed):
    """
    合成演示数据源（最终兜底）。

    - 无需任何 API key 或网络连接
    - 基于 GBM 生成日线 OHLCV
    - 同一标的始终返回相同数据（确定性）
    - 不支持分钟级别（返回空列表）
    """

    market = Market.US  # 支持所有市场，market 属性仅供标识

    def __init__(self, market: Market = Market.US) -> None:
        self._market = market

    async def get_bars(
        self,
        symbol: str,
        frequency: Frequency,
        start: date,
        end: date,
    ) -> list[Bar]:
        if frequency not in (Frequency.DAY_1, Frequency.WEEK_1):
            return []

        base_price = _BASE_PRICES.get(symbol.upper(), _DEFAULT_PRICE)
        seed = _seed_for(symbol)

        # 预生成从 _EPOCH 到 end 的完整序列
        total_days = (end - _EPOCH).days + 2
        prices = _build_price_series(base_price, total_days, seed)

        bars = _prices_to_bars(prices, start, end, symbol, self._market, frequency, seed)
        logger.info(
            "Using synthetic demo data",
            feed="DemoDataFeed",
            symbol=symbol,
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
        return []

    @property
    def name(self) -> str:
        return "DemoDataFeed"
