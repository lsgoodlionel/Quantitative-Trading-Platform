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

from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:  # 仅类型标注，避免 service 在导入期就拉起归档模块
    from app.data.archive import ArchiveHandler, ArchiveKey, Gap

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

        `settings.archive_enabled` 关闭（默认）时走 `fetch_direct`，
        行为与本地归档上线前**完全一致**；开启时先查本地归档、只补缺口。
        """
        from app.core.config import settings

        if not settings.archive_enabled:
            bars, _ = await self.fetch_direct(symbol, market, frequency, start, end, use_cache)
            return bars
        return await self._get_bars_archived(symbol, market, frequency, start, end, use_cache)

    async def fetch_direct(
        self,
        symbol: str,
        market: Market,
        frequency: Frequency,
        start: date,
        end: date,
        use_cache: bool = True,
    ) -> tuple[list[Bar], bool]:
        """
        原始取数路径（TimescaleDB 缓存 → 数据源链 → 演示数据兜底）。

        额外返回 `is_synthetic`：为 True 表示 bar 来自 DemoDataFeed 合成数据，
        调用方**不得**把它写进归档 —— 合成数据一旦落盘就再也分辨不出来了。

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
                    return cached, False

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
        is_synthetic = False
        if not bars:
            demo = self._registry.get_demo_feed(market)
            try:
                bars = await demo.get_bars(symbol, frequency, start, end)
                is_synthetic = bool(bars)
            except Exception as e:
                logger.error("Demo feed also failed", error=str(e))

        # 写入缓存（只缓存真实数据，不缓存合成数据以避免污染）
        if bars and use_cache:
            saved = await self._repo.save_bars(bars)
            logger.debug("Cached bars", symbol=symbol, count=saved)

        return bars, is_synthetic

    async def _get_bars_archived(
        self,
        symbol: str,
        market: Market,
        frequency: Frequency,
        start: date,
        end: date,
        use_cache: bool,
    ) -> list[Bar]:
        """
        归档优先取数：完整命中直接返回；部分命中只补两端缺口并写回归档。

        任一环节出错都退回 `fetch_direct` —— 归档是加速手段，不该成为取数的
        单点故障。
        """
        from app.data.archive import ArchiveError, ArchiveKey, boundary_gaps, get_archive

        archive = get_archive()
        if archive is None:
            bars, _ = await self.fetch_direct(symbol, market, frequency, start, end, use_cache)
            return bars

        try:
            key = ArchiveKey(symbol=symbol, market=market, frequency=frequency)
            coverage = archive.coverage(key)
            archived = archive.read(key, start, end) if coverage else []
            gaps = boundary_gaps(coverage, start, end)
        except (ArchiveError, ValueError) as e:
            logger.warning("Archive read failed, falling back online", symbol=symbol, error=str(e))
            bars, _ = await self.fetch_direct(symbol, market, frequency, start, end, use_cache)
            return bars

        if archived and not gaps:
            _warn_if_sparse(symbol, archived, start, end)
            logger.debug("Archive hit", symbol=symbol, count=len(archived))
            return archived

        fetched = await self._fill_gaps(key, gaps, use_cache, archive)
        return _merge_bars(archived, fetched, start, end)

    async def _fill_gaps(
        self, key: ArchiveKey, gaps: list[Gap], use_cache: bool, archive: ArchiveHandler
    ) -> list[Bar]:
        """逐段在线补齐并写回归档（合成数据不写回）。"""
        from app.data.archive import ArchiveError

        fetched: list[Bar] = []
        for gap in gaps:
            bars, is_synthetic = await self.fetch_direct(
                key.symbol, key.market, key.frequency, gap.start, gap.end, use_cache
            )
            if not bars:
                continue
            fetched.extend(bars)
            if is_synthetic:
                continue
            try:
                archive.write(key, bars)
            except ArchiveError as e:
                logger.warning("Archive write failed", symbol=key.symbol, error=str(e))
        return fetched

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


# 日线归档里每个自然日至少应有约 0.5 根 bar（周末/假日占掉约 2/7）；
# 低于这个比例说明归档内部很可能有空洞，而 boundary_gaps 只看两端、看不到它。
_SPARSE_ARCHIVE_RATIO = 0.4


def _warn_if_sparse(symbol: str, archived: list[Bar], start: date, end: date) -> None:
    """
    归档「完整命中」但 bar 数明显偏少时告警。

    `boundary_gaps` 刻意不看内部空洞（否则周末会让归档每次都退化成全量请求），
    代价是「某天下载失败留下的洞」会被静默吞掉。这里至少让它在日志里留下痕迹，
    而不是让回测悄悄少几根 bar。
    """
    expected_days = max(1, (end - start).days + 1)
    if len(archived) / expected_days >= _SPARSE_ARCHIVE_RATIO:
        return
    logger.warning(
        "Archive looks sparse; may have internal holes — 用 archive download 重下该区间",
        symbol=symbol, bars=len(archived), span_days=expected_days,
    )


def _merge_bars(
    archived: list[Bar], fetched: list[Bar], start: date, end: date
) -> list[Bar]:
    """按时间合并归档与在线两份 bar（在线覆盖归档），裁剪到 [start, end] 并升序。"""
    merged: dict = {b.time: b for b in archived}
    merged.update({b.time: b for b in fetched})
    return sorted(
        (b for b in merged.values() if start <= b.time.date() <= end),
        key=lambda b: b.time,
    )


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
