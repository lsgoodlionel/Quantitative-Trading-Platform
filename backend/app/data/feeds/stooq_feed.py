"""
Stooq 数据源 — 全球免费日/周线历史（无需 API key）

通过 CSV 下载接口获取，覆盖美股/港股（及部分 A 股指数）。
- 美股: aapl.us
- 港股: 0700.hk
仅日/周线，无分钟级、无实时推送。作为 US/HK 的又一免费备用，
提升整个平台的数据冗余度。

接口: https://stooq.com/q/d/l/?s={symbol}&d1={YYYYMMDD}&d2={YYYYMMDD}&i={d|w}
"""

from __future__ import annotations

import io
from datetime import date, datetime, timezone

import httpx
import pandas as pd

from app.core.logging import get_logger
from app.data.feeds.base import DataFeed
from app.data.models import Bar, Frequency, Market, SymbolInfo, Tick

logger = get_logger(__name__)

_BASE_URL = "https://stooq.com/q/d/l/"
_TIMEOUT = 12.0
# 无 UA 时 stooq 对部分 IP 返回 404；带 UA 则返回 200（限流时为 HTML，_csv_to_bars 会安全返回空）
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; QuantBot/1.0)"}

# Stooq 仅支持日/周线
_FREQ_TO_INTERVAL: dict[Frequency, str] = {
    Frequency.DAY_1: "d",
    Frequency.WEEK_1: "w",
}


def _to_stooq_symbol(symbol: str, market: Market) -> str:
    """内部代码 → Stooq 代码格式。"""
    s = symbol.strip().lower()
    if market == Market.US:
        return s if s.endswith(".us") else f"{s}.us"
    if market == Market.HK:
        # 港股 4 位数字 + .hk（腾讯 00700 → 0700.hk）
        digits = "".join(c for c in s if c.isdigit())
        if digits:
            return f"{int(digits):04d}.hk"
        return s if s.endswith(".hk") else f"{s}.hk"
    return s


def _csv_to_bars(text: str, symbol: str, market: Market, frequency: Frequency) -> list[Bar]:
    if not text or text.strip().lower().startswith("no data") or "Date" not in text:
        return []
    df = pd.read_csv(io.StringIO(text))
    if df.empty or "Close" not in df.columns:
        return []
    bars: list[Bar] = []
    for _, row in df.iterrows():
        close = row.get("Close")
        if close is None or pd.isna(close):
            continue
        try:
            dt = datetime.fromisoformat(str(row["Date"])).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        bars.append(
            Bar(
                time=dt,
                symbol=symbol,
                market=market,
                frequency=frequency,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(close),
                volume=int(row.get("Volume", 0) or 0),
            )
        )
    return bars


class StooqDataFeed(DataFeed):
    """Stooq 免费日/周线历史数据源（US/HK）。异步 httpx 拉取，不阻塞事件循环。"""

    market = Market.US

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
            raise ValueError(f"Stooq 仅支持日/周线，不支持: {frequency}")

        params = {
            "s": _to_stooq_symbol(symbol, self.market),
            "d1": start.strftime("%Y%m%d"),
            "d2": end.strftime("%Y%m%d"),
            "i": _FREQ_TO_INTERVAL[frequency],
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
            resp = await client.get(_BASE_URL, params=params)
            resp.raise_for_status()
            text = resp.text

        bars = _csv_to_bars(text, symbol, self.market, frequency)
        if not bars:
            logger.warning("Stooq returned empty data", symbol=params["s"])
        else:
            logger.info("Fetched historical bars", feed="stooq", symbol=params["s"], count=len(bars))
        return bars

    async def get_latest_bar(self, symbol: str, frequency: Frequency) -> Bar | None:
        from datetime import timedelta

        end = date.today()
        start = end - timedelta(days=10)
        bars = await self.get_bars(symbol, frequency, start, end)
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
