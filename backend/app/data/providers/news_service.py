"""
新闻 + 财报/分红日历服务（Wave-3f / A4）

职责:
  1. 按市场路由：US/HK → yfinance，A → akshare
  2. 统一封装为 {symbol, market, count, items/events, warnings} 响应
  3. 数据源失败降级为 warning，不硬失败（尽力覆盖）

对外暴露 NewsCalendarService.{get_news, get_earnings, get_dividends}。
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.data.providers import akshare_calendar_provider as ak_cal
from app.data.providers import news_provider as yf_news
from app.data.providers.news_calendar_models import (
    CompanyNewsResponse,
    DividendCalendarResponse,
    EarningsCalendarResponse,
)

logger = get_logger(__name__)

_SUPPORTED_MARKETS = {"US", "HK", "A"}


def _validate(symbol: str, market: str) -> tuple[str, str]:
    market = market.upper()
    if market not in _SUPPORTED_MARKETS:
        raise ValueError(f"不支持的市场: {market}（可选 US/HK/A）")
    symbol = symbol.strip()
    if not symbol:
        raise ValueError("标的代码不能为空")
    return symbol, market


class NewsCalendarService:
    """新闻 + 日历统一入口。无状态，可直接实例化。"""

    async def get_news(
        self, symbol: str, market: str = "US", limit: int = 20
    ) -> CompanyNewsResponse:
        symbol, market = _validate(symbol, market)
        warnings: list[str] = []
        items = []
        if market == "A":
            # yfinance 不覆盖 A 股新闻，返回空 + 提示
            warnings.append("A 股暂无公司新闻数据源")
        else:
            params = {"symbol": symbol, "market": market, "limit": limit}
            try:
                items = await yf_news.YFinanceNewsFetcher.fetch_data(params)
            except Exception as e:  # noqa: BLE001
                logger.warning("news fetch failed", symbol=symbol, error=str(e))
                warnings.append(f"新闻获取失败: {e}")
        return CompanyNewsResponse(
            symbol=symbol.upper(),
            market=market,
            count=len(items),
            items=items,
            warnings=warnings,
        )

    async def get_earnings(
        self, symbol: str, market: str = "US", limit: int = 12
    ) -> EarningsCalendarResponse:
        symbol, market = _validate(symbol, market)
        fetcher = (
            ak_cal.AkShareEarningsFetcher
            if market == "A"
            else yf_news.YFinanceEarningsFetcher
        )
        params = {"symbol": symbol, "market": market, "limit": limit}
        warnings: list[str] = []
        events = []
        try:
            events = await fetcher.fetch_data(params)
        except Exception as e:  # noqa: BLE001
            logger.warning("earnings fetch failed", symbol=symbol, error=str(e))
            warnings.append(f"财报日历获取失败: {e}")
        return EarningsCalendarResponse(
            symbol=symbol.upper(),
            market=market,
            count=len(events),
            events=events,
            warnings=warnings,
        )

    async def get_dividends(
        self, symbol: str, market: str = "US", limit: int = 12
    ) -> DividendCalendarResponse:
        symbol, market = _validate(symbol, market)
        fetcher = (
            ak_cal.AkShareDividendFetcher
            if market == "A"
            else yf_news.YFinanceDividendFetcher
        )
        params = {"symbol": symbol, "market": market, "limit": limit}
        warnings: list[str] = []
        events = []
        try:
            events = await fetcher.fetch_data(params)
        except Exception as e:  # noqa: BLE001
            logger.warning("dividends fetch failed", symbol=symbol, error=str(e))
            warnings.append(f"分红日历获取失败: {e}")
        return DividendCalendarResponse(
            symbol=symbol.upper(),
            market=market,
            count=len(events),
            events=events,
            warnings=warnings,
        )
