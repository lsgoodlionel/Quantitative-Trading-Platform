"""
DataService — 统一数据服务

负责:
1. 数据源路由: 根据市场选择正确的 DataFeed
2. 缓存策略: 先查 TimescaleDB，缓存命中则跳过 API 调用
3. 数据回填: 自动补全缺失时间段
4. 备用数据源: 主数据源失败时切换备用
5. 实时流管理: WebSocket 连接生命周期

数据源优先级:
  美股历史: Alpaca → yfinance (备用)
  港股历史: Futu   → yfinance (备用)
  美股实时: Alpaca WebSocket
  港股实时: Futu 订阅 (5秒轮询桥接)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import date, timedelta
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.data.feeds.akshare_feed import AkShareDataFeed
from app.data.feeds.alpaca import AlpacaDataFeed
from app.data.feeds.base import DataFeed
from app.data.feeds.demo_feed import DemoDataFeed
from app.data.feeds.futu import FutuDataFeed
from app.data.feeds.yfinance_feed import YFinanceDataFeed
from app.data.models import Bar, Frequency, Market, SymbolInfo, Tick
from app.data.storage.timescale import TimeseriesRepository

logger = get_logger(__name__)


class DataService:
    """
    数据服务统一入口。

    通过依赖注入 (FastAPI Depends) 获取数据库会话，
    通过 FeedRegistry 获取正确的数据源。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = TimeseriesRepository(session)
        from app.data.source_registry import DataSourceRegistry
        self._registry = DataSourceRegistry.instance()

    async def get_bars(
        self,
        symbol: str,
        market: Market,
        frequency: Frequency,
        start: date,
        end: date,
        use_cache: bool = True,
    ) -> list[Bar]:
        """
        获取历史 K 线。

        缓存策略:
        1. 查 TimescaleDB，命中则直接返回
        2. 缓存未命中 → 调用数据源 API
        3. 写入 TimescaleDB 供下次缓存命中
        """
        if use_cache:
            cached = await self._repo.get_bars(symbol, market, frequency, start, end)
            if cached:
                # 验证缓存覆盖率: 至少覆盖请求时间范围的 50% 日历天
                # 避免日线/周线仅有少量边界数据时错误地命中缓存
                expected_days = max(1, (end - start).days + 1)
                coverage = len(cached) / expected_days
                if coverage >= 0.5:
                    logger.debug("Cache hit", symbol=symbol, count=len(cached))
                    return cached

        # 缓存未命中，按配置的有序数据源链逐个尝试（动态切换 / 手动强制）
        chain = self._registry.get_feed_chain(market)
        bars: list[Bar] = []

        for feed in chain:
            try:
                bars = await feed.get_bars(symbol, frequency, start, end)
                if bars:
                    break
            except Exception as e:
                logger.warning(
                    "Data source failed, trying next",
                    feed=feed.name, error=str(e), symbol=symbol,
                )

        # 所有真实数据源失败 → 使用合成演示数据兜底（平台永不断供）
        if not bars:
            demo = self._registry.get_demo_feed(market)
            try:
                bars = await demo.get_bars(symbol, frequency, start, end)
            except Exception as e:
                logger.error("Demo feed also failed", error=str(e))

        # 写入缓存（只缓存真实数据，不缓存合成数据以避免污染）
        if bars and use_cache:
            saved = await self._repo.save_bars(bars)
            logger.debug("Cached bars", symbol=symbol, count=saved)

        return bars

    async def get_latest_bar(
        self,
        symbol: str,
        market: Market,
        frequency: Frequency,
    ) -> Bar | None:
        """获取最新 K 线（优先数据库，再调 API）。"""
        cached = await self._repo.get_latest_bar(symbol, market, frequency)
        if cached:
            return cached

        # 按配置源链逐个尝试
        for feed in self._registry.get_feed_chain(market):
            try:
                bar = await feed.get_latest_bar(symbol, frequency)
                if bar:
                    return bar
            except Exception as e:
                logger.warning("get_latest_bar failed", feed=feed.name, error=str(e))
        return None

    async def get_latest_tick(self, symbol: str, market: Market) -> Tick | None:
        for feed in self._registry.get_feed_chain(market):
            try:
                tick = await feed.get_latest_tick(symbol)
                if tick:
                    return tick
            except Exception:
                continue
        return None

    async def subscribe_bars(
        self,
        symbols: list[str],
        market: Market,
        frequency: Frequency,
    ) -> AsyncIterator[Bar]:
        """实时 K 线订阅，写库并 yield 给调用方。选链中首个支持实时的源。"""
        feed: DataFeed | None = next(
            (f for f in self._registry.get_feed_chain(market) if f.supports_realtime), None
        )
        if feed is None:
            logger.warning("No realtime feed available", market=market)
            return

        async for bar in feed.subscribe_bars(symbols, frequency):
            await self._repo.save_bars([bar])
            yield bar

    async def search_symbols(self, query: str, market: Market | None) -> list[SymbolInfo]:
        results: list[SymbolInfo] = []
        markets = [market] if market else list(Market)
        for m in markets:
            chain = self._registry.get_feed_chain(m)
            if not chain:
                continue
            try:
                found = await chain[0].search_symbols(query)
                results.extend(found)
            except Exception as e:
                logger.debug("Symbol search failed", market=m, error=str(e))
        return results

    async def backfill(
        self,
        symbol: str,
        market: Market,
        frequency: Frequency,
        days: int = 365,
    ) -> int:
        """历史数据回填（首次初始化用）。"""
        end = date.today()
        start = end - timedelta(days=days)
        bars = await self.get_bars(symbol, market, frequency, start, end, use_cache=False)
        return len(bars)


class _FeedRegistry:
    """数据源注册表（单例）。"""

    _instance: _FeedRegistry | None = None

    def __init__(self) -> None:
        self._alpaca = AlpacaDataFeed()
        self._futu_hk = FutuDataFeed(Market.HK)
        self._yf_us = YFinanceDataFeed(Market.US)
        self._yf_hk = YFinanceDataFeed(Market.HK)
        self._akshare = AkShareDataFeed()
        self._demo_us = DemoDataFeed(Market.US)
        self._demo_hk = DemoDataFeed(Market.HK)
        self._demo_a = DemoDataFeed(Market.A)

    @classmethod
    def instance(cls) -> _FeedRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_feeds(self, market: Market) -> tuple[DataFeed, DataFeed | None]:
        """
        返回 (主数据源, 备用数据源)。

        美股: Alpaca → yfinance
        港股: Futu   → yfinance
        A股:  akshare（免费日/周线）

        所有真实数据源失败后，DataService 会自动调用 get_demo_feed() 兜底。
        """
        if market == Market.US:
            return self._alpaca, self._yf_us
        if market == Market.HK:
            return self._futu_hk, self._yf_hk
        if market == Market.A:
            return self._akshare, None
        raise ValueError(f"Unknown market: {market}")

    def get_demo_feed(self, market: Market) -> DemoDataFeed:
        """返回对应市场的合成演示数据源（最终兜底）。"""
        if market == Market.US:
            return self._demo_us
        if market == Market.HK:
            return self._demo_hk
        return self._demo_a


# FastAPI Depends 工厂
async def get_data_service(session: AsyncSession) -> DataService:
    return DataService(session)
