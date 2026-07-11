"""
公司新闻流 API 端点（Wave-3f / A4）

  GET /news/{symbol}  — 单标的最近新闻（US/HK via yfinance；A 股暂无源）

风格对齐 endpoints/fundamentals.py：Pydantic 响应模型 + try/except → HTTPException。
数据层复用 app.data.providers.NewsCalendarService。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query

from app.core.logging import get_logger
from app.data.providers import NewsCalendarService
from app.data.providers.news_calendar_models import CompanyNewsResponse

logger = get_logger(__name__)

router = APIRouter(tags=["News"])


@router.get("/{symbol}", response_model=CompanyNewsResponse)
async def get_company_news(
    symbol: str = Path(..., description="标的代码，如 AAPL / 00700"),
    market: Literal["US", "HK", "A"] = Query("US", description="市场"),
    limit: int = Query(20, ge=1, le=100, description="返回最近 N 条"),
) -> CompanyNewsResponse:
    """获取单标的公司新闻流。"""
    try:
        return await NewsCalendarService().get_news(
            symbol=symbol, market=market, limit=limit
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 — 数据源整体不可用
        logger.error("news fetch failed", symbol=symbol, market=market, error=str(e))
        raise HTTPException(status_code=503, detail=f"新闻数据源不可用: {e}") from e
