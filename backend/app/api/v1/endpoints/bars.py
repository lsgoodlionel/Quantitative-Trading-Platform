from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import numpy as np
import pandas as pd

from app.core.database import get_db
from app.data.models import Bar as BarModel
from app.data.models import Frequency, Market
from app.data.service import DataService
# Note: indicators imported lazily inside the endpoint to avoid circular imports
# (app.strategy.__init__ → StrategyContext → backtest.engine → strategy.__init__)

router = APIRouter()


# --- Response schemas ---

class BarResponse(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float | None = None

    @classmethod
    def from_model(cls, bar: BarModel) -> "BarResponse":
        return cls(
            time=bar.time.isoformat(),
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            vwap=bar.vwap,
        )


class BarsListResponse(BaseModel):
    symbol: str
    market: str
    frequency: str
    count: int
    bars: list[BarResponse]


class SymbolSearchResult(BaseModel):
    symbol: str
    market: str
    name: str
    name_zh: str | None = None
    exchange: str | None = None
    currency: str | None = None


class MarketOverviewItem(BaseModel):
    symbol: str
    market: str
    name: str
    name_zh: str | None = None
    price: float | None = None
    prev_close: float | None = None
    change_pct: float | None = None  # 涨跌幅 %


class MarketOverviewResponse(BaseModel):
    A: list[MarketOverviewItem]
    HK: list[MarketOverviewItem]
    US: list[MarketOverviewItem]


# --- Dependency ---

def get_service(session: AsyncSession = Depends(get_db)) -> DataService:
    return DataService(session)


# --- Routes ---

@router.get("", response_model=BarsListResponse)
async def get_bars(
    symbol: Annotated[str, Query(description="股票代码，如 AAPL / 00700 / 0700.HK")],
    market: Annotated[Market, Query(description="市场: US / HK / A")],
    frequency: Annotated[Frequency, Query(description="频率: 1m/5m/15m/1h/1d")] = Frequency.DAY_1,
    start: Annotated[date | None, Query(description="开始日期 YYYY-MM-DD")] = None,
    end: Annotated[date | None, Query(description="结束日期 YYYY-MM-DD")] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
    svc: DataService = Depends(get_service),
) -> BarsListResponse:
    end_date = end or date.today()
    start_date = start or (end_date - timedelta(days=365))

    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start must be before end")

    try:
        bars = await svc.get_bars(symbol, market, frequency, start_date, end_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Data feed error: {e}")

    bars = bars[:limit]
    return BarsListResponse(
        symbol=symbol,
        market=market.value,
        frequency=frequency.value,
        count=len(bars),
        bars=[BarResponse.from_model(b) for b in bars],
    )


@router.get("/latest")
async def get_latest_bar(
    symbol: Annotated[str, Query()],
    market: Annotated[Market, Query()],
    frequency: Annotated[Frequency, Query()] = Frequency.DAY_1,
    svc: DataService = Depends(get_service),
) -> BarResponse | None:
    bar = await svc.get_latest_bar(symbol, market, frequency)
    return BarResponse.from_model(bar) if bar else None


@router.get("/symbols/search", response_model=list[SymbolSearchResult])
async def search_symbols(
    q: Annotated[str, Query(min_length=1, description="搜索关键词")],
    market: Annotated[Market | None, Query()] = None,
    svc: DataService = Depends(get_service),
) -> list[SymbolSearchResult]:
    from app.data.symbol_dict import get_cn_name

    symbols = await svc.search_symbols(q, market)
    return [
        SymbolSearchResult(
            symbol=s.symbol,
            market=s.market.value,
            name=s.name,
            name_zh=s.name_zh or get_cn_name(s.symbol, s.market),
            exchange=s.exchange,
            currency=s.currency,
        )
        for s in symbols
    ]


@router.get("/market-overview", response_model=MarketOverviewResponse)
async def get_market_overview(svc: DataService = Depends(get_service)) -> MarketOverviewResponse:
    """
    返回三大市场预设股票列表，含最新价格和涨跌幅。
    用于行情页三板块展示。
    """
    import asyncio as _asyncio

    from app.data.symbol_dict import A_PANEL, HK_PANEL, US_PANEL

    async def _fetch_item(symbol: str, market_enum: Market, cn_name: str) -> MarketOverviewItem:
        try:
            bar = await svc.get_latest_bar(symbol, market_enum, Frequency.DAY_1)
            if bar:
                chg = ((bar.close - bar.open) / bar.open * 100) if bar.open else None
                return MarketOverviewItem(
                    symbol=symbol,
                    market=market_enum.value,
                    name=cn_name,
                    name_zh=cn_name,
                    price=round(bar.close, 3),
                    prev_close=round(bar.open, 3),
                    change_pct=round(chg, 2) if chg is not None else None,
                )
        except Exception:
            pass
        return MarketOverviewItem(
            symbol=symbol,
            market=market_enum.value,
            name=cn_name,
            name_zh=cn_name,
        )

    a_tasks = [_fetch_item(s, Market.A, cn) for s, cn in A_PANEL]
    hk_tasks = [_fetch_item(s, Market.HK, cn) for s, cn in HK_PANEL]
    us_tasks = [_fetch_item(s, Market.US, cn) for s, cn in US_PANEL]

    a_items, hk_items, us_items = await _asyncio.gather(
        _asyncio.gather(*a_tasks),
        _asyncio.gather(*hk_tasks),
        _asyncio.gather(*us_tasks),
    )

    return MarketOverviewResponse(A=list(a_items), HK=list(hk_items), US=list(us_items))


# ── 实时行情（Spot Quotes）────────────────────────────────────

class SpotQuote(BaseModel):
    symbol: str
    market: str
    name: str
    name_zh: str | None = None
    price: float | None = None
    prev_close: float | None = None
    change_pct: float | None = None
    change: float | None = None        # 涨跌额
    volume: int | None = None
    high: float | None = None
    low: float | None = None
    source: str = "demo"               # "realtime" / "delayed" / "daily" / "demo"
    updated_at: str | None = None


class SpotQuotesResponse(BaseModel):
    A: list[SpotQuote]
    HK: list[SpotQuote]
    US: list[SpotQuote]


async def _fetch_a_spot() -> list[SpotQuote]:
    """AkShare 沪深A股实时行情，一次拉取全市场。"""
    import logging
    from app.data.symbol_dict import A_PANEL
    import asyncio

    _log = logging.getLogger(__name__)
    panel_codes = {s for s, _ in A_PANEL}

    def _fetch() -> dict:
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot_em()
            # 列名: 代码, 名称, 最新价, 涨跌额, 涨跌幅, 最高, 最低, 成交量
            result: dict = {}
            for _, row in df.iterrows():
                code = str(row.get("代码", "")).strip()
                if code not in panel_codes:
                    continue
                price = row.get("最新价")
                prev = row.get("昨收")
                chg_pct = row.get("涨跌幅")
                chg = row.get("涨跌额")
                result[code] = {
                    "price": float(price) if price and str(price) != "nan" else None,
                    "prev_close": float(prev) if prev and str(prev) != "nan" else None,
                    "change_pct": float(chg_pct) if chg_pct and str(chg_pct) != "nan" else None,
                    "change": float(chg) if chg and str(chg) != "nan" else None,
                    "volume": int(float(row.get("成交量", 0) or 0)),
                    "high": float(row.get("最高")) if row.get("最高") and str(row.get("最高")) != "nan" else None,
                    "low": float(row.get("最低")) if row.get("最低") and str(row.get("最低")) != "nan" else None,
                }
            return result
        except Exception as exc:
            _log.warning("_fetch_a_spot error: %s: %s", type(exc).__name__, exc)
            return {}

    loop = asyncio.get_running_loop()
    try:
        spot_map = await asyncio.wait_for(
            loop.run_in_executor(None, _fetch), timeout=8.0
        )
    except Exception as exc:
        _log.warning("_fetch_a_spot timeout: %s", exc)
        spot_map = {}

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    quotes = []
    for symbol, cn in A_PANEL:
        d = spot_map.get(symbol, {})
        quotes.append(SpotQuote(
            symbol=symbol,
            market="A",
            name=cn,
            name_zh=cn,
            source="realtime" if d else "demo",
            updated_at=now if d else None,
            **{k: v for k, v in d.items()},
        ))
    return quotes


async def _fetch_hk_spot() -> list[SpotQuote]:
    """yfinance 港股行情批量下载（日线收盘价，约15分钟延迟）。"""
    import logging
    from app.data.symbol_dict import HK_PANEL
    import asyncio

    _log = logging.getLogger(__name__)

    # Build Yahoo Finance 4-digit format: 00700 → 0700.HK
    panel_map = {f"{int(s):04d}.HK": (s, cn) for s, cn in HK_PANEL}
    yf_syms = list(panel_map.keys())

    def _fetch_batch() -> dict:
        """Single batch download — avoids per-ticker rate limits."""
        try:
            import yfinance as yf
            df = yf.download(
                yf_syms,
                period="2d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                timeout=15,
            )
            result: dict = {}
            for yf_sym in yf_syms:
                try:
                    closes = df[yf_sym]["Close"].dropna()
                    highs  = df[yf_sym]["High"].dropna()
                    lows   = df[yf_sym]["Low"].dropna()
                    if closes.empty:
                        continue
                    price = float(closes.iloc[-1])
                    prev  = float(closes.iloc[-2]) if len(closes) >= 2 else None
                    high  = float(highs.iloc[-1]) if not highs.empty else None
                    low   = float(lows.iloc[-1]) if not lows.empty else None
                    chg_pct = ((price - prev) / prev * 100) if prev else None
                    result[yf_sym] = {
                        "price": round(price, 3),
                        "prev_close": round(prev, 3) if prev else None,
                        "change_pct": round(chg_pct, 2) if chg_pct else None,
                        "change": round(price - prev, 3) if prev else None,
                        "high": round(high, 3) if high else None,
                        "low": round(low, 3) if low else None,
                    }
                except Exception:
                    pass
            return result
        except Exception as exc:
            _log.warning("_fetch_hk_spot batch error: %s: %s", type(exc).__name__, exc)
            return {}

    loop = asyncio.get_running_loop()
    try:
        spot_map = await asyncio.wait_for(
            loop.run_in_executor(None, _fetch_batch), timeout=18.0
        )
    except Exception as exc:
        _log.warning("_fetch_hk_spot timeout: %s", exc)
        spot_map = {}

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    quotes = []
    for yf_sym, (symbol, cn) in panel_map.items():
        d = spot_map.get(yf_sym, {})
        quotes.append(SpotQuote(
            symbol=symbol,
            market="HK",
            name=cn,
            name_zh=cn,
            source="daily" if d else "demo",
            updated_at=now if d else None,
            **{k: v for k, v in d.items() if v is not None},
        ))
    return quotes


async def _fetch_us_spot() -> list[SpotQuote]:
    """Alpaca 美股实时最新成交价（IEX feed 免费账户 15 分钟延迟）。"""
    import logging
    from app.data.symbol_dict import US_PANEL
    import asyncio

    _log = logging.getLogger(__name__)
    symbols = [s for s, _ in US_PANEL]

    def _fetch() -> dict:
        try:
            import redis as sync_redis
            from app.core.config import settings as cfg
            r = sync_redis.from_url(cfg.redis_url, decode_responses=True)
            raw = r.hgetall("broker_config:alpaca")
            r.close()
            if not raw:
                return {}
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockLatestTradeRequest
            client = StockHistoricalDataClient(
                api_key=raw.get("api_key", ""),
                secret_key=raw.get("api_secret", ""),
            )
            req = StockLatestTradeRequest(symbol_or_symbols=symbols)
            trades = client.get_stock_latest_trade(req)
            result: dict = {}
            for sym, trade in trades.items():
                result[sym] = {
                    "price": float(trade.price),
                    "volume": int(trade.size),
                }
            return result
        except Exception as exc:
            _log.warning("_fetch_us_spot error: %s: %s", type(exc).__name__, exc)
            return {}

    loop = asyncio.get_running_loop()
    try:
        spot_map = await asyncio.wait_for(
            loop.run_in_executor(None, _fetch), timeout=10.0
        )
    except Exception as exc:
        _log.warning("_fetch_us_spot timeout/error: %s", exc)
        spot_map = {}

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    quotes = []
    for symbol, cn in US_PANEL:
        d = spot_map.get(symbol, {})
        quotes.append(SpotQuote(
            symbol=symbol,
            market="US",
            name=cn,
            name_zh=cn,
            source="realtime" if d else "demo",
            updated_at=now if d else None,
            **{k: v for k, v in d.items()},
        ))
    return quotes


@router.get("/spot", response_model=SpotQuotesResponse)
async def get_spot_quotes() -> SpotQuotesResponse:
    """
    三市实时/延迟行情快照：
    - A股:  AkShare 东方财富实时行情（交易日实时）
    - 港股:  yfinance 延迟约15分钟
    - 美股:  Alpaca IEX 最新成交（免费账户约15分钟延迟，付费账户实时）

    建议轮询间隔: 5–10 秒（A股/美股盘中），30 秒（港股/休市）
    """
    import asyncio as _asyncio
    a_quotes, hk_quotes, us_quotes = await _asyncio.gather(
        _fetch_a_spot(),
        _fetch_hk_spot(),
        _fetch_us_spot(),
    )
    return SpotQuotesResponse(A=a_quotes, HK=hk_quotes, US=us_quotes)


@router.get("/indicators")
async def compute_indicators(  # noqa: C901
    symbol: Annotated[str, Query()],
    market: Annotated[Market, Query()],
    frequency: Annotated[Frequency, Query()] = Frequency.DAY_1,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    indicators: Annotated[str, Query(description="逗号分隔指标名: sma,ema,rsi,macd,bb,atr,adx,stoch,cci,obv,vwap,williams_r,roc,mfi,donchian,keltner")] = "sma,rsi,macd",
    svc: DataService = Depends(get_service),
) -> dict:
    """
    计算技术指标并返回时序数据。
    每个指标返回 [{time, value}, …] 格式。
    多值指标（MACD、布林带等）返回子键。
    """
    end_date = end or date.today()
    start_date = start or (end_date - timedelta(days=365))

    bars = await svc.get_bars(symbol, market, frequency, start_date, end_date)
    if not bars:
        raise HTTPException(404, detail=f"No bars for {symbol}")

    # Import from app.quant.indicators — standalone module, no circular dependencies
    from app.quant import indicators as ind

    # 构建 DataFrame
    df = pd.DataFrame([{
        "time": b.time.isoformat(), "open": b.open, "high": b.high,
        "low": b.low, "close": b.close, "volume": b.volume,
    } for b in bars]).set_index("time")

    requested = {s.strip().lower() for s in indicators.split(",")}
    result: dict[str, object] = {"time": list(df.index)}

    def _series(s: pd.Series) -> list[float | None]:
        return [None if (v is None or (isinstance(v, float) and np.isnan(v))) else round(float(v), 4) for v in s]

    # ── 均线 ──
    if "sma" in requested or "sma20" in requested:
        result["sma20"] = _series(ind.sma(df, 20))
        result["sma60"] = _series(ind.sma(df, 60))
    if "ema" in requested:
        result["ema12"] = _series(ind.ema(df, 12))
        result["ema26"] = _series(ind.ema(df, 26))

    # ── RSI ──
    if "rsi" in requested:
        result["rsi"] = _series(ind.rsi(df, 14))

    # ── MACD ──
    if "macd" in requested:
        macd_line, sig_line, hist = ind.macd(df)
        result["macd"] = _series(macd_line)
        result["macd_signal"] = _series(sig_line)
        result["macd_hist"] = _series(hist)

    # ── Bollinger Bands ──
    if "bb" in requested or "bollinger" in requested:
        upper, mid, lower = ind.bollinger_bands(df)
        result["bb_upper"] = _series(upper)
        result["bb_mid"]   = _series(mid)
        result["bb_lower"] = _series(lower)

    # ── ATR ──
    if "atr" in requested:
        result["atr"] = _series(ind.atr(df, 14))

    # ── ADX ──
    if "adx" in requested:
        result["adx"] = _series(ind.adx(df, 14))

    # ── Stochastic ──
    if "stoch" in requested:
        k, d = ind.stochastic(df)
        result["stoch_k"] = _series(k)
        result["stoch_d"] = _series(d)

    # ── CCI ──
    if "cci" in requested:
        result["cci"] = _series(ind.cci(df, 20))

    # ── OBV ──
    if "obv" in requested:
        result["obv"] = _series(ind.obv(df))

    # ── VWAP ──
    if "vwap" in requested:
        result["vwap"] = _series(ind.vwap(df, 20))

    # ── Williams %R ──
    if "williams_r" in requested:
        result["williams_r"] = _series(ind.williams_r(df, 14))

    # ── ROC ──
    if "roc" in requested:
        result["roc"] = _series(ind.roc(df, 12))

    # ── MFI ──
    if "mfi" in requested:
        result["mfi"] = _series(ind.mfi(df, 14))

    # ── Donchian ──
    if "donchian" in requested:
        upper, mid, lower = ind.donchian_channels(df)
        result["donchian_upper"] = _series(upper)
        result["donchian_mid"]   = _series(mid)
        result["donchian_lower"] = _series(lower)

    # ── Keltner ──
    if "keltner" in requested:
        upper, mid, lower = ind.keltner_channels(df)
        result["keltner_upper"] = _series(upper)
        result["keltner_mid"]   = _series(mid)
        result["keltner_lower"] = _series(lower)

    return result


@router.post("/backfill")
async def backfill_symbol(
    symbol: str,
    market: Market,
    frequency: Frequency = Frequency.DAY_1,
    days: Annotated[int, Query(ge=1, le=3650)] = 365,
    svc: DataService = Depends(get_service),
) -> dict:
    """历史数据回填端点（初始化用）。"""
    count = await svc.backfill(symbol, market, frequency, days)
    return {"symbol": symbol, "market": market.value, "bars_saved": count}
