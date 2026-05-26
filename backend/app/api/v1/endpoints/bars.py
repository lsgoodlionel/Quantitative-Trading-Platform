from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.data.models import Bar as BarModel
from app.data.models import Frequency, Market
from app.data.service import DataService

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
