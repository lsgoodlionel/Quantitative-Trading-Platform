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
    exchange: str | None = None
    currency: str | None = None


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
    symbols = await svc.search_symbols(q, market)
    return [
        SymbolSearchResult(
            symbol=s.symbol,
            market=s.market.value,
            name=s.name,
            exchange=s.exchange,
            currency=s.currency,
        )
        for s in symbols
    ]


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
