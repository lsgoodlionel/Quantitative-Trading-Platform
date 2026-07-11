"""
财报 + 分红日历 API 端点（Wave-3f / A4）

  GET /calendar/earnings   — 单标的财报日历（US/HK via yfinance；A 股 via akshare）
  GET /calendar/dividends  — 单标的分红日历（同上）

按标的查询（与 yfinance 逐标的能力对齐）。风格对齐 endpoints/fundamentals.py。
数据层复用 app.data.providers.NewsCalendarService。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.core.logging import get_logger
from app.data.providers import NewsCalendarService
from app.data.providers.news_calendar_models import (
    DividendCalendarResponse,
    EarningsCalendarResponse,
)

logger = get_logger(__name__)

router = APIRouter(tags=["Calendar"])


@router.get("/earnings", response_model=EarningsCalendarResponse)
async def get_earnings_calendar(
    symbol: str = Query(..., description="标的代码，如 AAPL / 600519"),
    market: Literal["US", "HK", "A"] = Query("US", description="市场"),
    limit: int = Query(12, ge=1, le=40, description="返回最近/未来 N 期"),
) -> EarningsCalendarResponse:
    """获取单标的财报日历。"""
    try:
        return await NewsCalendarService().get_earnings(
            symbol=symbol, market=market, limit=limit
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.error("earnings fetch failed", symbol=symbol, market=market, error=str(e))
        raise HTTPException(status_code=503, detail=f"财报日历数据源不可用: {e}") from e


@router.get("/dividends", response_model=DividendCalendarResponse)
async def get_dividend_calendar(
    symbol: str = Query(..., description="标的代码，如 AAPL / 600519"),
    market: Literal["US", "HK", "A"] = Query("US", description="市场"),
    limit: int = Query(12, ge=1, le=40, description="返回最近/未来 N 期"),
) -> DividendCalendarResponse:
    """获取单标的分红日历。"""
    try:
        return await NewsCalendarService().get_dividends(
            symbol=symbol, market=market, limit=limit
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.error("dividends fetch failed", symbol=symbol, market=market, error=str(e))
        raise HTTPException(status_code=503, detail=f"分红日历数据源不可用: {e}") from e
