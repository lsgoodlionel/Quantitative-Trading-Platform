from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal


class Market(str, Enum):
    US = "US"
    HK = "HK"
    A = "A"


class Frequency(str, Enum):
    MIN_1 = "1m"
    MIN_5 = "5m"
    MIN_15 = "15m"
    MIN_30 = "30m"
    HOUR_1 = "1h"
    HOUR_4 = "4h"
    DAY_1 = "1d"
    WEEK_1 = "1w"


@dataclass(frozen=True)
class Bar:
    """统一 K 线数据结构 — 参考 refs/alpaca-py/alpaca/data/models/bars.py"""

    time: datetime
    symbol: str
    market: Market
    frequency: Frequency
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float | None = None
    turnover: float | None = None   # 成交额（港股/A股有）
    trade_count: int | None = None  # 成交笔数

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"high {self.high} < low {self.low} for {self.symbol}")
        if self.volume < 0:
            raise ValueError(f"Negative volume for {self.symbol}")

    @property
    def mid(self) -> float:
        return (self.high + self.low) / 2

    @property
    def change(self) -> float:
        return self.close - self.open

    @property
    def change_pct(self) -> float:
        return self.change / self.open if self.open else 0.0


@dataclass(frozen=True)
class Tick:
    """Tick 报价数据"""

    time: datetime
    symbol: str
    market: Market
    last_price: float
    last_size: int = 0
    bid_price: float | None = None
    ask_price: float | None = None
    bid_size: int | None = None
    ask_size: int | None = None

    @property
    def spread(self) -> float | None:
        if self.bid_price is not None and self.ask_price is not None:
            return self.ask_price - self.bid_price
        return None


@dataclass
class SymbolInfo:
    """股票基础信息"""

    symbol: str
    market: Market
    name: str
    name_zh: str | None = None
    exchange: str | None = None
    currency: str | None = None
    lot_size: int = 1              # 最小交易单位（港股多数为100）
    is_active: bool = True
    extra: dict = field(default_factory=dict)
