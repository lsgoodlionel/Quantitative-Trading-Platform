"""
AkShare A 股财报/分红日历提供者（Wave-3f / A4）

数据来源:
  - ak.stock_yysj_em(symbol="沪深A股", date=报告期)  → 预约披露时间（财报日历，按报告期，过滤代码）
  - ak.stock_fhps_detail_em(symbol=纯代码)           → 分红送配详情（除权除息/登记日/派息/股息率）

A 股接口不稳定且字段中文，全部 best-effort：失败返回空并记 warning，不硬失败。
A 股无公司新闻源（yfinance 不覆盖 A 股新闻），前端对 A 股隐藏新闻面板。
"""

from __future__ import annotations

from datetime import date as DateType
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.core.logging import get_logger
from app.data.providers.akshare_provider import _bare_code
from app.data.providers.base import Fetcher
from app.data.providers.news_calendar_models import (
    CalendarQueryParams,
    DividendEvent,
    EarningsEvent,
)

logger = get_logger(__name__)

# 财报日历回溯的报告期数（每期一次网络调用，控制在合理范围）
_EARNINGS_PERIODS = 4


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _as_date(value: Any) -> DateType | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    return None if pd.isna(ts) else ts.date()


def _recent_report_periods(count: int) -> list[str]:
    """最近 count 个季度末报告期（YYYYMMDD），从上一个已过季度末往前。"""
    today = datetime.now(tz=timezone.utc).date()
    quarter_ends = [(3, 31), (6, 30), (9, 30), (12, 31)]
    periods: list[str] = []
    year = today.year
    # 从今年往回逐季枚举，收集已到达的季度末
    while len(periods) < count and year > today.year - 4:
        for month, day in reversed(quarter_ends):
            qe = DateType(year, month, day)
            if qe <= today:
                periods.append(qe.strftime("%Y%m%d"))
                if len(periods) >= count:
                    break
        year -= 1
    return periods


class AkShareEarningsFetcher(Fetcher[CalendarQueryParams, list[EarningsEvent]]):
    """A 股财报日历（预约披露时间，跨报告期聚合并过滤代码）。"""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> CalendarQueryParams:
        return CalendarQueryParams(**params)

    @staticmethod
    def extract_data(query: CalendarQueryParams) -> list[tuple[str, pd.DataFrame]]:
        import akshare as ak

        code = _bare_code(query.symbol)
        rows: list[tuple[str, pd.DataFrame]] = []
        for period in _recent_report_periods(_EARNINGS_PERIODS):
            try:
                df = ak.stock_yysj_em(symbol="沪深A股", date=period)
            except Exception as e:  # noqa: BLE001
                logger.debug("akshare yysj failed", period=period, error=str(e))
                continue
            if not isinstance(df, pd.DataFrame) or df.empty or "股票代码" not in df.columns:
                continue
            matched = df[df["股票代码"].astype(str).str.zfill(6) == code.zfill(6)]
            if not matched.empty:
                rows.append((period, matched))
        return rows

    @staticmethod
    def transform_data(
        query: CalendarQueryParams, data: list[tuple[str, pd.DataFrame]]
    ) -> list[EarningsEvent]:
        today = datetime.now(tz=timezone.utc).date()
        events: list[EarningsEvent] = []
        for period, df in data:
            row = df.iloc[0]
            report_date = _as_date(row.get("实际披露时间")) or _as_date(
                row.get("首次预约时间")
            )
            if report_date is None:
                continue
            events.append(
                EarningsEvent(
                    report_date=report_date,
                    symbol=query.symbol.upper(),
                    name=str(row.get("股票简称")) if row.get("股票简称") is not None else None,
                    period=f"{period[:4]}-{period[4:6]}-{period[6:]}",
                    is_upcoming=report_date >= today,
                )
            )
        events.sort(key=lambda e: e.report_date or today, reverse=True)
        return events[: query.limit]


class AkShareDividendFetcher(Fetcher[CalendarQueryParams, list[DividendEvent]]):
    """A 股分红日历（分红送配详情）。现金分红比例为「每 10 股派息」，转换为每股。"""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> CalendarQueryParams:
        return CalendarQueryParams(**params)

    @staticmethod
    def extract_data(query: CalendarQueryParams) -> pd.DataFrame:
        import akshare as ak

        try:
            df = ak.stock_fhps_detail_em(symbol=_bare_code(query.symbol))
        except Exception as e:  # noqa: BLE001
            logger.warning("akshare fhps failed", symbol=query.symbol, error=str(e))
            return pd.DataFrame()
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()

    @staticmethod
    def transform_data(
        query: CalendarQueryParams, data: pd.DataFrame
    ) -> list[DividendEvent]:
        if data.empty:
            return []
        today = datetime.now(tz=timezone.utc).date()
        events: list[DividendEvent] = []
        # 已按报告期降序返回；取最近 limit 条
        for _, row in data.head(query.limit).iterrows():
            ex_date = _as_date(row.get("除权除息日"))
            per_10 = _num(row.get("现金分红-现金分红比例"))
            amount = per_10 / 10.0 if per_10 is not None else None
            yield_pct = _num(row.get("现金分红-股息率"))
            # 股息率原始为百分比 → 分数
            dyield = yield_pct / 100.0 if yield_pct is not None else None
            period = row.get("报告期")
            events.append(
                DividendEvent(
                    ex_dividend_date=ex_date,
                    symbol=query.symbol.upper(),
                    amount=amount,
                    record_date=_as_date(row.get("股权登记日")),
                    declaration_date=_as_date(row.get("预案公告日"))
                    or _as_date(row.get("最新公告日期")),
                    dividend_yield=dyield,
                    period=str(_as_date(period) or period) if period is not None else None,
                    is_upcoming=ex_date is not None and ex_date >= today,
                )
            )
        return events
