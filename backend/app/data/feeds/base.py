from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import date, datetime

from app.data.models import Bar, Frequency, Market, SymbolInfo, Tick


class DataFeed(ABC):
    """
    统一数据源抽象基类。

    设计参考:
    - refs/backtrader/backtrader/feed.py — DataFeed 生命周期模式
    - refs/zipline-reloaded/zipline/data/ — Bundle/DataFeed 分层设计
    - refs/alpaca-py/alpaca/data/ — 实际API数据结构

    每个市场（美股/港股/A股）实现一个具体子类。
    """

    market: Market  # 子类必须声明

    @abstractmethod
    async def get_bars(
        self,
        symbol: str,
        frequency: Frequency,
        start: date,
        end: date,
    ) -> list[Bar]:
        """拉取历史 K 线，返回按时间升序的列表。"""
        ...

    @abstractmethod
    async def get_latest_bar(self, symbol: str, frequency: Frequency) -> Bar | None:
        """获取最新一根 K 线（用于实盘策略初始化）。"""
        ...

    async def get_latest_tick(self, symbol: str) -> Tick | None:
        """获取最新 Tick，默认不支持（子类按需覆写）。"""
        return None

    async def subscribe_bars(
        self,
        symbols: list[str],
        frequency: Frequency,
    ) -> AsyncIterator[Bar]:
        """实时 K 线订阅。默认不支持（实时数据源子类覆写）。"""
        raise NotImplementedError(f"{self.__class__.__name__} does not support real-time bars")
        yield  # 让 Python 识别为 async generator

    async def subscribe_ticks(self, symbols: list[str]) -> AsyncIterator[Tick]:
        """实时 Tick 订阅。默认不支持。"""
        raise NotImplementedError(f"{self.__class__.__name__} does not support real-time ticks")
        yield

    async def search_symbols(self, query: str) -> list[SymbolInfo]:
        """股票代码/名称搜索。默认返回空列表。"""
        return []

    @property
    def supports_realtime(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return self.__class__.__name__
