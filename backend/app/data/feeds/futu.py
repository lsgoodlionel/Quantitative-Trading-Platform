"""
富途 OpenAPI 港股（+ 美股）数据源

需要安装并运行 OpenD 桌面程序: https://openapi.futunn.com/futu-api-doc/

历史K线: refs/py-futu-api/futu/quote/open_quote_context.py request_history_kline()
实时订阅: refs/py-futu-api/futu/quote/open_quote_context.py subscribe()
数据结构: refs/py-futu-api/futu/common/constant.py KLType / SubType

港股代码格式: "HK.00700" (腾讯)
美股代码格式: "US.AAPL"
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import date, datetime, timezone

import pandas as pd

from app.core.config import settings
from app.core.logging import get_logger
from app.data.feeds.base import DataFeed
from app.data.models import Bar, Frequency, Market, SymbolInfo, Tick

logger = get_logger(__name__)

# 内部频率 → 富途 KLType 字符串
# 参考: refs/py-futu-api/futu/common/constant.py KLType
_FREQ_TO_KLTYPE: dict[Frequency, str] = {
    Frequency.MIN_1: "K_1M",
    Frequency.MIN_5: "K_5M",
    Frequency.MIN_15: "K_15M",
    Frequency.MIN_30: "K_30M",
    Frequency.HOUR_1: "K_60M",
    Frequency.DAY_1: "K_DAY",
    Frequency.WEEK_1: "K_WEEK",
}

# 内部频率 → 富途 SubType（实时订阅）
_FREQ_TO_SUBTYPE: dict[Frequency, str] = {
    Frequency.MIN_1: "K_1M",
    Frequency.MIN_5: "K_5M",
    Frequency.MIN_15: "K_15M",
    Frequency.MIN_30: "K_30M",
    Frequency.HOUR_1: "K_60M",
    Frequency.DAY_1: "K_DAY",
}


def _symbol_to_futu(symbol: str, market: Market) -> str:
    """将内部代码格式转为富途格式: AAPL → US.AAPL, 00700 → HK.00700"""
    if "." in symbol and symbol.split(".")[0] in ("HK", "US", "SH", "SZ"):
        return symbol  # 已是富途格式
    prefix = market.value
    return f"{prefix}.{symbol}"


def _futu_row_to_bar(row: pd.Series, symbol: str, market: Market, frequency: Frequency) -> Bar:
    """将富途 DataFrame 行转为内部 Bar。"""
    time_key: str = row["time_key"]
    ts = datetime.strptime(time_key, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return Bar(
        time=ts,
        symbol=symbol,
        market=market,
        frequency=frequency,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=int(row["volume"]),
        turnover=float(row["turnover"]) if "turnover" in row and pd.notna(row["turnover"]) else None,
    )


class FutuDataFeed(DataFeed):
    """
    富途 OpenAPI 数据源，主要用于港股，兼支持美股。

    需要先启动 OpenD: https://openapi.futunn.com/futu-api-doc/intro/opend.html

    支持:
    - 历史 K 线 (get_bars) — request_history_kline，自动分页
    - 实时 K 线订阅 (subscribe_bars) — SubType.K_*
    - 最新 Tick (get_latest_tick) — get_stock_quote
    """

    market = Market.HK  # 主市场，也可处理 US

    def __init__(self, market: Market = Market.HK) -> None:
        self.market = market
        self._ctx: object | None = None

    def _get_ctx(self) -> object:
        """惰性初始化行情上下文。OpenD 必须在本机运行。"""
        if self._ctx is None:
            try:
                from futu import OpenQuoteContext
            except ImportError as e:
                raise RuntimeError(
                    "futu-api not installed: pip install futu-api\n"
                    "Also requires OpenD running: https://openapi.futunn.com/futu-api-doc/"
                ) from e

            self._ctx = OpenQuoteContext(host=settings.futu_host, port=settings.futu_port)
            logger.info("Futu OpenD connected", host=settings.futu_host, port=settings.futu_port)
        return self._ctx

    async def get_bars(
        self,
        symbol: str,
        frequency: Frequency,
        start: date,
        end: date,
    ) -> list[Bar]:
        """
        拉取历史 K 线，自动处理分页。

        参考: refs/py-futu-api/futu/quote/open_quote_context.py request_history_kline()
        富途每次最多返回 1000 条，通过 page_req_key 分页。
        """
        if frequency not in _FREQ_TO_KLTYPE:
            raise ValueError(f"Unsupported frequency for Futu: {frequency}")

        kltype = _FREQ_TO_KLTYPE[frequency]
        futu_code = _symbol_to_futu(symbol, self.market)
        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

        loop = asyncio.get_event_loop()
        all_bars: list[Bar] = []
        page_req_key = None

        while True:
            def _fetch(prk: object = page_req_key) -> tuple:
                ctx = self._get_ctx()
                from futu import KLType, AuType
                kl_type = getattr(KLType, kltype)
                return ctx.request_history_kline(  # type: ignore[attr-defined]
                    futu_code,
                    start=start_str,
                    end=end_str,
                    ktype=kl_type,
                    autype=AuType.QFQ,  # 前复权
                    max_count=1000,
                    page_req_key=prk,
                )

            ret, data, next_key = await loop.run_in_executor(None, _fetch)

            if ret != 0:  # RET_OK = 0
                logger.error("Futu get_bars failed", symbol=symbol, error=data)
                break

            if isinstance(data, pd.DataFrame) and not data.empty:
                for _, row in data.iterrows():
                    all_bars.append(_futu_row_to_bar(row, symbol, self.market, frequency))

            if next_key is None:
                break
            page_req_key = next_key

        all_bars.sort(key=lambda b: b.time)
        logger.info(
            "Fetched historical bars",
            feed="futu",
            symbol=symbol,
            frequency=frequency.value,
            count=len(all_bars),
        )
        return all_bars

    async def get_latest_bar(self, symbol: str, frequency: Frequency) -> Bar | None:
        bars = await self.get_bars(symbol, frequency, date.today(), date.today())
        return bars[-1] if bars else None

    async def get_latest_tick(self, symbol: str) -> Tick | None:
        """获取最新报价。参考: refs/py-futu-api/futu/quote/open_quote_context.py get_stock_quote()"""
        futu_code = _symbol_to_futu(symbol, self.market)
        loop = asyncio.get_event_loop()

        def _fetch() -> tuple:
            ctx = self._get_ctx()
            return ctx.get_stock_quote([futu_code])  # type: ignore[attr-defined]

        ret, data = await loop.run_in_executor(None, _fetch)
        if ret != 0 or not isinstance(data, pd.DataFrame) or data.empty:
            return None

        row = data.iloc[0]
        return Tick(
            time=datetime.now(timezone.utc),
            symbol=symbol,
            market=self.market,
            last_price=float(row.get("last_price", 0)),
            bid_price=float(row.get("bid_price", 0)) or None,
            ask_price=float(row.get("ask_price", 0)) or None,
            bid_size=int(row.get("bid_1_vol", 0)) or None,
            ask_size=int(row.get("ask_1_vol", 0)) or None,
        )

    async def subscribe_bars(
        self,
        symbols: list[str],
        frequency: Frequency,
    ) -> AsyncIterator[Bar]:
        """
        实时 K 线订阅。

        参考: refs/py-futu-api/futu/quote/open_quote_context.py subscribe()
        推送通过回调 handler 转换为 asyncio.Queue。
        """
        if frequency not in _FREQ_TO_SUBTYPE:
            raise ValueError(f"Realtime subscription unsupported for frequency: {frequency}")

        try:
            from futu import SubType, RET_OK
        except ImportError as e:
            raise RuntimeError("futu-api not installed") from e

        futu_codes = [_symbol_to_futu(s, self.market) for s in symbols]
        subtype = getattr(SubType, _FREQ_TO_SUBTYPE[frequency])
        queue: asyncio.Queue[Bar] = asyncio.Queue(maxsize=1000)
        loop = asyncio.get_event_loop()

        class _KlineHandler:
            def on_recv_rsp(self, rsp_pb: object) -> tuple:
                from futu import RET_OK as OK
                # rsp_pb 由 futu-api 内部解析，返回 (ret, data)
                return OK, None

        ctx = self._get_ctx()
        # 注册 K 线 push handler
        # refs/py-futu-api/futu/quote/open_quote_context.py line ~200
        ctx.set_handler(_KlineHandler())  # type: ignore[attr-defined]

        def _subscribe() -> None:
            ctx.subscribe(futu_codes, [subtype], is_first_push=True)  # type: ignore[attr-defined]

        await loop.run_in_executor(None, _subscribe)
        logger.info("Futu realtime subscribed", symbols=futu_codes, subtype=str(subtype))

        # 富途使用回调模型，通过轮询最新 K 线桥接到 async 生成器
        # 生产环境建议换用 futu 的异步扩展或 OpenD gRPC streaming
        try:
            while True:
                await asyncio.sleep(5)  # 每5秒查询最新K线
                for symbol in symbols:
                    bar = await self.get_latest_bar(symbol, frequency)
                    if bar:
                        await queue.put(bar)
                while not queue.empty():
                    yield queue.get_nowait()
        finally:
            def _unsub() -> None:
                ctx.unsubscribe(futu_codes, [subtype])  # type: ignore[attr-defined]
            await loop.run_in_executor(None, _unsub)

    @property
    def supports_realtime(self) -> bool:
        return True

    async def search_symbols(self, query: str) -> list[SymbolInfo]:
        """港股代码/名称搜索。"""
        try:
            from futu import OpenQuoteContext, SimpleFilter, StockField, SortDir
        except ImportError:
            return []

        loop = asyncio.get_event_loop()
        q = query.upper()

        def _search() -> list[SymbolInfo]:
            ctx = self._get_ctx()
            simple_filter = SimpleFilter()
            simple_filter.keep_stock = True  # type: ignore[attr-defined]
            ret, data = ctx.get_stock_filter(  # type: ignore[attr-defined]
                Market.HK.value, [simple_filter]
            )
            if ret != 0 or not isinstance(data, pd.DataFrame):
                return []
            mask = (
                data["code"].str.upper().str.contains(q) |
                data["name"].str.upper().str.contains(q)
            )
            rows = data[mask].head(20)
            return [
                SymbolInfo(
                    symbol=row["code"],
                    market=Market.HK,
                    name=row["name"],
                    currency="HKD",
                    lot_size=int(row.get("lot_size", 100)),
                )
                for _, row in rows.iterrows()
            ]

        try:
            return await loop.run_in_executor(None, _search)
        except Exception as e:
            logger.warning("Futu symbol search failed", error=str(e))
            return []
