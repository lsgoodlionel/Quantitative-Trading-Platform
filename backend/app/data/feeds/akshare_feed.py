"""
AkShare A 股数据源 — 沪深 A 股历史 K 线（免费，无需 API key）

代码格式:
  上海: 600000 (无后缀) → akshare 使用 "sh600000"
  深圳: 000001 (无后缀) → akshare 使用 "sz000001"
  外部传入: "600000" 或 "SH600000" 均可

频率映射:
  1d  → "daily"
  1w  → "weekly"
  1M  → "monthly"  （分钟级数据 akshare 限制较多，当前仅支持日/周/月）

注意事项:
  - 复权类型: "qfq" (前复权) 为默认，可改 "hfq" (后复权) 或 "" (不复权)
  - 免费接口，有频率限制，建议间隔 0.5s+
  - 日期格式: akshare 使用 "YYYYMMDD"
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import pandas as pd

from app.core.logging import get_logger
from app.data.feeds.base import DataFeed
from app.data.models import Bar, Frequency, Market, SymbolInfo, Tick

logger = get_logger(__name__)

_FREQ_MAP: dict[Frequency, str] = {
    Frequency.DAY_1: "daily",
    Frequency.WEEK_1: "weekly",
}

# A 股市场判断规则
_SH_PREFIXES = ("6",)   # 沪市主板 6xxxxx
_SZ_PREFIXES = ("0", "3")  # 深市主板 0xxxxx / 创业板 3xxxxx


def _to_ak_symbol(symbol: str) -> str:
    """将外部代码 ('600000' / 'SH600000') 统一转为 akshare 格式 ('sh600000')。"""
    s = symbol.upper().strip()
    if s.startswith("SH"):
        return "sh" + s[2:]
    if s.startswith("SZ"):
        return "sz" + s[2:]
    # 裸数字，按前缀判断
    digits = s.lstrip("0") or "0"
    if s[0] in _SH_PREFIXES:
        return "sh" + s
    return "sz" + s


def _df_to_bars(df: pd.DataFrame, symbol: str, frequency: Frequency) -> list[Bar]:
    bars: list[Bar] = []
    for _, row in df.iterrows():
        try:
            dt = datetime.strptime(str(row["日期"]), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, KeyError):
            continue
        bars.append(
            Bar(
                time=dt,
                symbol=symbol,
                market=Market.A,
                frequency=frequency,
                open=float(row["开盘"]),
                high=float(row["最高"]),
                low=float(row["最低"]),
                close=float(row["收盘"]),
                volume=int(float(row.get("成交量", 0))),
            )
        )
    return bars


class AkShareDataFeed(DataFeed):
    """
    akshare A 股历史数据源。

    当前支持日线/周线历史数据（前复权）。
    分钟级 K 线后续按需扩展。
    """

    market = Market.A

    def __init__(self, adjust: str = "qfq") -> None:
        """
        Args:
            adjust: 复权方式，"qfq" 前复权（默认）/ "hfq" 后复权 / "" 不复权
        """
        self._adjust = adjust

    async def get_bars(
        self,
        symbol: str,
        frequency: Frequency,
        start: date,
        end: date,
    ) -> list[Bar]:
        if frequency not in _FREQ_MAP:
            raise ValueError(
                f"AkShare feed 当前仅支持 daily/weekly，不支持: {frequency.value}"
            )

        ak_symbol = _to_ak_symbol(symbol)
        period = _FREQ_MAP[frequency]
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        def _fetch() -> pd.DataFrame:
            try:
                import akshare as ak
            except ImportError as e:
                raise RuntimeError("akshare not installed: pip install akshare") from e

            return ak.stock_zh_a_hist(
                symbol=ak_symbol[2:],       # akshare 不需要 sh/sz 前缀，只要数字部分
                period=period,
                start_date=start_str,
                end_date=end_str,
                adjust=self._adjust,
            )

        loop = asyncio.get_event_loop()
        df: pd.DataFrame = await loop.run_in_executor(None, _fetch)

        if df.empty:
            logger.warning("AkShare returned empty data", symbol=ak_symbol)
            return []

        bars = _df_to_bars(df, symbol, frequency)
        logger.info(
            "Fetched A-share bars via akshare",
            symbol=ak_symbol,
            period=period,
            count=len(bars),
        )
        return bars

    async def get_latest_bar(self, symbol: str, frequency: Frequency) -> Bar | None:
        bars = await self.get_bars(symbol, frequency, date.today(), date.today())
        return bars[-1] if bars else None

    async def get_latest_tick(self, symbol: str) -> Tick | None:
        return None

    @property
    def supports_realtime(self) -> bool:
        return False

    async def search_symbols(self, query: str) -> list[SymbolInfo]:
        """
        A 股代码搜索。
        优先使用本地 symbol_dict（涵盖 200+ 只主要 A 股），
        若 akshare 可用则补充搜索全量股票池。
        参考: refs/OpenBB/openbb_platform/providers/finviz/openbb_finviz/models/equity_screener.py
        """
        from app.data.symbol_dict import search_by_cn_name

        # 1. 本地词典优先（低延迟）
        local = [
            SymbolInfo(symbol=sym, name=cn, name_zh=cn, market=Market.A)
            for sym, _, cn in search_by_cn_name(query, Market.A)
        ]
        if local:
            return local[:20]

        # 2. akshare 补充搜索（如已安装）
        def _akshare_search() -> list[SymbolInfo]:
            try:
                import akshare as ak
            except ImportError:
                return []
            try:
                df = ak.stock_info_a_code_name()
                matched = df[
                    df["code"].str.contains(query, na=False)
                    | df["name"].str.contains(query, na=False)
                ].head(20)
                return [
                    SymbolInfo(symbol=row["code"], name=row["name"], market=Market.A)
                    for _, row in matched.iterrows()
                ]
            except Exception as e:
                logger.warning("AkShare symbol search failed", error=str(e))
                return []

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _akshare_search)
