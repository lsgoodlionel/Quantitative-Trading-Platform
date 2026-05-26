"""
Alpaca 美股数据源

历史数据: refs/alpaca-py/alpaca/data/historical/stock.py — StockHistoricalDataClient
实时行情: refs/alpaca-py/alpaca/data/live/stock.py — StockDataStream
数据模型: refs/alpaca-py/alpaca/data/models/bars.py — Bar / BarSet

免费账户: IEX feed，15分钟延迟
付费账户: SIP feed，实时
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.logging import get_logger
from app.data.feeds.base import DataFeed
from app.data.models import Bar, Frequency, Market, SymbolInfo, Tick

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# 频率映射: 内部 Frequency → Alpaca TimeFrame 字符串
_FREQ_MAP: dict[Frequency, tuple[int, str]] = {
    Frequency.MIN_1: (1, "Minute"),
    Frequency.MIN_5: (5, "Minute"),
    Frequency.MIN_15: (15, "Minute"),
    Frequency.MIN_30: (30, "Minute"),
    Frequency.HOUR_1: (1, "Hour"),
    Frequency.HOUR_4: (4, "Hour"),
    Frequency.DAY_1: (1, "Day"),
    Frequency.WEEK_1: (1, "Week"),
}


def _to_bar(raw: object, symbol: str, frequency: Frequency) -> Bar:
    """将 alpaca-py Bar 对象转为内部 Bar。"""
    ts: datetime = raw.timestamp  # type: ignore[attr-defined]
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return Bar(
        time=ts,
        symbol=symbol,
        market=Market.US,
        frequency=frequency,
        open=float(raw.open),  # type: ignore[attr-defined]
        high=float(raw.high),  # type: ignore[attr-defined]
        low=float(raw.low),  # type: ignore[attr-defined]
        close=float(raw.close),  # type: ignore[attr-defined]
        volume=int(raw.volume),  # type: ignore[attr-defined]
        vwap=float(raw.vwap) if getattr(raw, "vwap", None) is not None else None,
        trade_count=int(raw.trade_count) if getattr(raw, "trade_count", None) is not None else None,
    )


class AlpacaDataFeed(DataFeed):
    """
    Alpaca 美股数据源。

    支持:
    - 历史 K 线 (get_bars)
    - 最新 K 线 (get_latest_bar)
    - 实时行情 WebSocket (subscribe_bars / subscribe_ticks)
    """

    market = Market.US

    def __init__(self) -> None:
        self._hist_client: object | None = None
        self._stream_client: object | None = None

    def _get_hist_client(self) -> object:
        if self._hist_client is None:
            try:
                from alpaca.data.historical.stock import StockHistoricalDataClient
            except ImportError as e:
                raise RuntimeError("alpaca-py not installed: pip install alpaca-py") from e

            self._hist_client = StockHistoricalDataClient(
                api_key=settings.alpaca_api_key or None,
                secret_key=settings.alpaca_secret_key or None,
            )
        return self._hist_client

    async def get_bars(
        self,
        symbol: str,
        frequency: Frequency,
        start: date,
        end: date,
    ) -> list[Bar]:
        """
        拉取历史 K 线。

        参考: refs/alpaca-py/alpaca/data/historical/stock.py get_stock_bars()
        """
        if frequency not in _FREQ_MAP:
            raise ValueError(f"Unsupported frequency for Alpaca: {frequency}")

        amount, unit = _FREQ_MAP[frequency]

        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        except ImportError as e:
            raise RuntimeError("alpaca-py not installed") from e

        unit_obj = getattr(TimeFrameUnit, unit)
        tf = TimeFrame(amount, unit_obj)

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=datetime.combine(start, datetime.min.time()).replace(tzinfo=timezone.utc),
            end=datetime.combine(end, datetime.max.time()).replace(tzinfo=timezone.utc),
            limit=10_000,
        )

        client = self._get_hist_client()
        loop = asyncio.get_event_loop()

        # alpaca-py 是同步 SDK，在线程池中运行避免阻塞事件循环
        bar_set = await loop.run_in_executor(None, client.get_stock_bars, request)  # type: ignore[attr-defined]

        bars_raw = bar_set.data.get(symbol, [])
        result = [_to_bar(b, symbol, frequency) for b in bars_raw]
        result.sort(key=lambda b: b.time)

        logger.info(
            "Fetched historical bars",
            feed="alpaca",
            symbol=symbol,
            frequency=frequency.value,
            count=len(result),
        )
        return result

    async def get_latest_bar(self, symbol: str, frequency: Frequency) -> Bar | None:
        """参考: refs/alpaca-py/alpaca/data/historical/stock.py get_stock_latest_bar()"""
        try:
            from alpaca.data.requests import StockLatestBarRequest
        except ImportError as e:
            raise RuntimeError("alpaca-py not installed") from e

        request = StockLatestBarRequest(symbol_or_symbols=symbol)
        client = self._get_hist_client()
        loop = asyncio.get_event_loop()
        latest = await loop.run_in_executor(None, client.get_stock_latest_bar, request)  # type: ignore[attr-defined]

        raw = latest.get(symbol)
        if raw is None:
            return None
        return _to_bar(raw, symbol, frequency)

    async def subscribe_bars(
        self,
        symbols: list[str],
        frequency: Frequency,
    ) -> AsyncIterator[Bar]:
        """
        实时 K 线订阅 via WebSocket。

        参考: refs/alpaca-py/alpaca/data/live/stock.py StockDataStream
        注意: Alpaca 实时流只推送分钟级 K 线（非 daily），daily 通过定时拉取实现。
        """
        try:
            from alpaca.data.enums import DataFeed as AlpacaFeed
            from alpaca.data.live.stock import StockDataStream
        except ImportError as e:
            raise RuntimeError("alpaca-py not installed") from e

        queue: asyncio.Queue[Bar] = asyncio.Queue(maxsize=1000)

        async def on_bar(raw_bar: object) -> None:
            sym = getattr(raw_bar, "symbol", symbols[0] if symbols else "")
            bar = _to_bar(raw_bar, sym, frequency)
            await queue.put(bar)

        stream = StockDataStream(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            feed=AlpacaFeed.IEX,
        )
        stream.subscribe_bars(on_bar, *symbols)

        # 在后台任务中运行 WebSocket 循环
        task = asyncio.create_task(stream._run_forever())  # type: ignore[attr-defined]

        try:
            while True:
                bar = await queue.get()
                yield bar
        finally:
            task.cancel()

    async def subscribe_ticks(self, symbols: list[str]) -> AsyncIterator[Tick]:
        """实时 Trade Tick 订阅。"""
        try:
            from alpaca.data.enums import DataFeed as AlpacaFeed
            from alpaca.data.live.stock import StockDataStream
        except ImportError as e:
            raise RuntimeError("alpaca-py not installed") from e

        queue: asyncio.Queue[Tick] = asyncio.Queue(maxsize=5000)

        async def on_trade(raw_trade: object) -> None:
            sym = getattr(raw_trade, "symbol", "")
            ts: datetime = getattr(raw_trade, "timestamp", datetime.now(timezone.utc))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            tick = Tick(
                time=ts,
                symbol=sym,
                market=Market.US,
                last_price=float(getattr(raw_trade, "price", 0)),
                last_size=int(getattr(raw_trade, "size", 0)),
            )
            await queue.put(tick)

        stream = StockDataStream(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            feed=AlpacaFeed.IEX,
        )
        stream.subscribe_trades(on_trade, *symbols)
        task = asyncio.create_task(stream._run_forever())  # type: ignore[attr-defined]

        try:
            while True:
                yield await queue.get()
        finally:
            task.cancel()

    @property
    def supports_realtime(self) -> bool:
        return bool(settings.alpaca_api_key and settings.alpaca_secret_key)

    async def search_symbols(self, query: str) -> list[SymbolInfo]:
        """通过 Alpaca assets API 搜索股票。"""
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.trading.requests import GetAssetsRequest
            from alpaca.trading.enums import AssetClass, AssetStatus
        except ImportError:
            return []

        client = TradingClient(
            api_key=settings.alpaca_api_key or None,
            secret_key=settings.alpaca_secret_key or None,
            paper=settings.alpaca_paper,
        )
        req = GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE)
        loop = asyncio.get_event_loop()
        assets = await loop.run_in_executor(None, client.get_all_assets, req)

        q = query.upper()
        matched = [
            a for a in assets
            if q in a.symbol.upper() or (a.name and q in a.name.upper())
        ][:20]

        return [
            SymbolInfo(
                symbol=a.symbol,
                market=Market.US,
                name=a.name or a.symbol,
                exchange=a.exchange.value if a.exchange else None,
                currency="USD",
                lot_size=1,
            )
            for a in matched
        ]
