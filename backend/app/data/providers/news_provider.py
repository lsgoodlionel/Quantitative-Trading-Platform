"""
yfinance 新闻 + 财报/分红日历提供者（Wave-3f / A4）— 覆盖 US / HK

数据来源:
  - ticker.news          → 公司新闻流（兼容新旧两种返回结构）
  - ticker.earnings_dates→ 财报日历（含 EPS 预期/实际/超预期）
  - ticker.calendar      → 下次财报日 / 除权除息日兜底
  - ticker.dividends     → 历史分红（除息日 + 每股金额）
  - ticker.info          → 名称 / 股息率 / 待发放分红兜底

yfinance 为阻塞库，extract_data 保持同步，由 base.Fetcher.fetch_data 放入线程池。
港股代码映射复用 yfinance_provider.to_yf_symbol（00700 → 0700.HK）。
"""

from __future__ import annotations

from datetime import date as DateType
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.core.logging import get_logger
from app.data.providers.base import Fetcher
from app.data.providers.news_calendar_models import (
    CalendarQueryParams,
    CompanyNewsItem,
    DividendEvent,
    EarningsEvent,
    NewsQueryParams,
)
from app.data.providers.yfinance_provider import to_yf_symbol

logger = get_logger(__name__)


def _num(value: Any) -> float | None:
    """安全转 float；NaN / None / 非数值 → None。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _as_date(value: Any) -> DateType | None:
    """任意日期表示 → date；失败 → None。"""
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    return None if pd.isna(ts) else ts.date()


def _parse_dt(value: Any) -> datetime | None:
    """epoch 秒 / ISO 字符串 → datetime；失败 → None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    return None if pd.isna(ts) else ts.to_pydatetime()


# ── 新闻 ─────────────────────────────────────────────────────────


def _normalize_news_item(raw: dict[str, Any]) -> CompanyNewsItem | None:
    """兼容 yfinance 新旧两种新闻结构 → CompanyNewsItem。"""
    if not isinstance(raw, dict):
        return None

    # 新版结构：数据嵌套在 raw["content"] 下
    content = raw.get("content")
    if isinstance(content, dict):
        provider = content.get("provider") or {}
        canonical = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
        thumb = content.get("thumbnail") or {}
        resolutions = thumb.get("resolutions") if isinstance(thumb, dict) else None
        thumb_url = resolutions[0].get("url") if resolutions else None
        title = content.get("title")
        if not title:
            return None
        return CompanyNewsItem(
            published_at=_parse_dt(content.get("pubDate") or content.get("displayTime")),
            title=title,
            publisher=provider.get("displayName") if isinstance(provider, dict) else None,
            summary=content.get("summary") or content.get("description"),
            url=canonical.get("url") if isinstance(canonical, dict) else None,
            thumbnail=thumb_url,
        )

    # 旧版结构：扁平字段
    title = raw.get("title")
    if not title:
        return None
    thumb = raw.get("thumbnail") or {}
    resolutions = thumb.get("resolutions") if isinstance(thumb, dict) else None
    thumb_url = resolutions[0].get("url") if resolutions else None
    related = raw.get("relatedTickers") or []
    return CompanyNewsItem(
        published_at=_parse_dt(raw.get("providerPublishTime")),
        title=title,
        publisher=raw.get("publisher"),
        url=raw.get("link"),
        thumbnail=thumb_url,
        symbols=[str(t) for t in related] if isinstance(related, list) else [],
    )


class YFinanceNewsFetcher(Fetcher[NewsQueryParams, list[CompanyNewsItem]]):
    """公司新闻流。"""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> NewsQueryParams:
        return NewsQueryParams(**params)

    @staticmethod
    def extract_data(query: NewsQueryParams) -> list[dict[str, Any]]:
        import yfinance as yf

        ticker = yf.Ticker(to_yf_symbol(query.symbol, query.market))
        try:
            news = ticker.news
        except Exception as e:  # noqa: BLE001 — 新闻接口偶发抛错
            logger.warning("yfinance news failed", symbol=query.symbol, error=str(e))
            return []
        return news if isinstance(news, list) else []

    @staticmethod
    def transform_data(
        query: NewsQueryParams, data: list[dict[str, Any]]
    ) -> list[CompanyNewsItem]:
        out: list[CompanyNewsItem] = []
        for raw in data[: query.limit]:
            item = _normalize_news_item(raw)
            if item is not None:
                out.append(item)
        return out


# ── 财报日历 ──────────────────────────────────────────────────────


class YFinanceEarningsFetcher(Fetcher[CalendarQueryParams, list[EarningsEvent]]):
    """财报日历（earnings_dates 为主，calendar 兜底下次财报日）。"""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> CalendarQueryParams:
        return CalendarQueryParams(**params)

    @staticmethod
    def extract_data(query: CalendarQueryParams) -> dict[str, Any]:
        import yfinance as yf

        ticker = yf.Ticker(to_yf_symbol(query.symbol, query.market))
        out: dict[str, Any] = {"dates": None, "calendar": {}, "name": None}
        try:
            df = ticker.earnings_dates
            out["dates"] = df if isinstance(df, pd.DataFrame) else None
        except Exception as e:  # noqa: BLE001
            logger.debug("earnings_dates failed", symbol=query.symbol, error=str(e))
        try:
            cal = ticker.calendar
            out["calendar"] = cal if isinstance(cal, dict) else {}
        except Exception as e:  # noqa: BLE001
            logger.debug("calendar failed", symbol=query.symbol, error=str(e))
        return out

    @staticmethod
    def transform_data(
        query: CalendarQueryParams, data: dict[str, Any]
    ) -> list[EarningsEvent]:
        today = datetime.now(tz=timezone.utc).date()
        events: list[EarningsEvent] = []
        seen: set[DateType] = set()

        df: pd.DataFrame | None = data.get("dates")
        if isinstance(df, pd.DataFrame) and not df.empty:
            for idx, row in df.head(query.limit).iterrows():
                rdate = _as_date(idx)
                if rdate is None or rdate in seen:
                    continue
                seen.add(rdate)
                events.append(
                    EarningsEvent(
                        report_date=rdate,
                        symbol=query.symbol.upper(),
                        period=str(rdate.year),
                        eps_estimate=_num(row.get("EPS Estimate")),
                        eps_actual=_num(row.get("Reported EPS")),
                        surprise_percent=_num(row.get("Surprise(%)")),
                        is_upcoming=rdate >= today,
                    )
                )

        # calendar 兜底：补下次财报日
        cal = data.get("calendar") or {}
        next_dates = cal.get("Earnings Date")
        if isinstance(next_dates, list):
            for nd in next_dates:
                rdate = _as_date(nd)
                if rdate is None or rdate in seen:
                    continue
                seen.add(rdate)
                events.append(
                    EarningsEvent(
                        report_date=rdate,
                        symbol=query.symbol.upper(),
                        eps_estimate=_num(cal.get("Earnings Average")),
                        is_upcoming=rdate >= today,
                    )
                )

        events.sort(key=lambda e: e.report_date or today, reverse=True)
        return events[: query.limit]


# ── 分红日历 ──────────────────────────────────────────────────────


class YFinanceDividendFetcher(Fetcher[CalendarQueryParams, list[DividendEvent]]):
    """分红日历（历史 dividends + calendar/info 兜底未来除息日）。"""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> CalendarQueryParams:
        return CalendarQueryParams(**params)

    @staticmethod
    def extract_data(query: CalendarQueryParams) -> dict[str, Any]:
        import yfinance as yf

        ticker = yf.Ticker(to_yf_symbol(query.symbol, query.market))
        out: dict[str, Any] = {"dividends": None, "calendar": {}, "info": {}}
        try:
            div = ticker.dividends
            out["dividends"] = div if isinstance(div, pd.Series) else None
        except Exception as e:  # noqa: BLE001
            logger.debug("dividends failed", symbol=query.symbol, error=str(e))
        try:
            cal = ticker.calendar
            out["calendar"] = cal if isinstance(cal, dict) else {}
        except Exception as e:  # noqa: BLE001
            logger.debug("calendar failed", symbol=query.symbol, error=str(e))
        try:
            info = ticker.info
            out["info"] = info if isinstance(info, dict) else {}
        except Exception as e:  # noqa: BLE001
            logger.debug("info failed", symbol=query.symbol, error=str(e))
        return out

    @staticmethod
    def transform_data(
        query: CalendarQueryParams, data: dict[str, Any]
    ) -> list[DividendEvent]:
        today = datetime.now(tz=timezone.utc).date()
        info = data.get("info") or {}
        dy = _num(info.get("dividendYield"))
        if dy is not None and dy > 1:  # 新版 yfinance 返回百分比 → 统一为分数
            dy = dy / 100.0
        name = info.get("shortName") or info.get("longName")

        events: list[DividendEvent] = []
        seen: set[DateType] = set()

        series: pd.Series | None = data.get("dividends")
        if isinstance(series, pd.Series) and not series.empty:
            tail = series.tail(query.limit)
            for idx, amount in tail.items():
                ex_date = _as_date(idx)
                if ex_date is None or ex_date in seen:
                    continue
                seen.add(ex_date)
                events.append(
                    DividendEvent(
                        ex_dividend_date=ex_date,
                        symbol=query.symbol.upper(),
                        name=name,
                        amount=_num(amount),
                        dividend_yield=dy,
                        is_upcoming=ex_date >= today,
                    )
                )

        # calendar 兜底：未来除息日 / 派息日
        cal = data.get("calendar") or {}
        ex_up = _as_date(cal.get("Ex-Dividend Date"))
        if ex_up is not None and ex_up not in seen:
            seen.add(ex_up)
            events.append(
                DividendEvent(
                    ex_dividend_date=ex_up,
                    symbol=query.symbol.upper(),
                    name=name,
                    amount=_num(info.get("lastDividendValue")),
                    payment_date=_as_date(cal.get("Dividend Date")),
                    dividend_yield=dy,
                    is_upcoming=ex_up >= today,
                )
            )

        events.sort(key=lambda e: e.ex_dividend_date or today, reverse=True)
        return events[: query.limit]
