"""
yfinance 基本面数据提供者（Wave-2a / A2）— 覆盖 US / HK

数据来源:
  - ticker.financials / .balance_sheet / .cashflow  → 三大报表（年度）
  - ticker.info                                     → 关键指标 / 市值 / 估值

设计: 每张报表一个 Fetcher 子类（transform_query→extract_data→transform_data）。
yfinance 为阻塞库，extract_data 保持同步，由 base.Fetcher.fetch_data 放入线程池。

港股代码映射: 00700 → 0700.HK（复用 yfinance_feed 约定）。
"""

from __future__ import annotations

from datetime import date as DateType
from typing import Any

import pandas as pd

from app.core.logging import get_logger
from app.data.providers.base import Fetcher
from app.data.providers.models import (
    BalanceSheetData,
    CashFlowData,
    FundamentalsQueryParams,
    IncomeStatementData,
    KeyMetricsData,
)

logger = get_logger(__name__)


def to_yf_symbol(symbol: str, market: str) -> str:
    """内部代码 → yfinance 代码。港股去前导零加 .HK。"""
    s = symbol.strip().upper()
    if market == "HK":
        clean = s.lstrip("0") or "0"
        return clean if clean.endswith(".HK") else f"{clean}.HK"
    return s


def _num(value: Any) -> float | None:
    """安全转 float；NaN / None / 非数值 → None。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _col_date(col: Any) -> DateType | None:
    """报表列名（Timestamp / str）→ date。"""
    try:
        ts = pd.Timestamp(col)
    except (ValueError, TypeError):
        return None
    return None if pd.isna(ts) else ts.date()


def _pick(series: pd.Series, *labels: str) -> float | None:
    """在一列（index=科目名）中按候选科目名依次取值。"""
    for label in labels:
        if label in series.index:
            val = _num(series.get(label))
            if val is not None:
                return val
    return None


def _iter_periods(df: pd.DataFrame | None, limit: int):
    """遍历报表 DataFrame 的期次列，yield (period_date, column_series)。"""
    if df is None or df.empty:
        return
    for col in list(df.columns)[:limit]:
        yield _col_date(col), df[col]


class YFinanceIncomeFetcher(Fetcher[FundamentalsQueryParams, list[IncomeStatementData]]):
    """利润表。"""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> FundamentalsQueryParams:
        return FundamentalsQueryParams(**params)

    @staticmethod
    def extract_data(query: FundamentalsQueryParams) -> pd.DataFrame:
        import yfinance as yf

        ticker = yf.Ticker(to_yf_symbol(query.symbol, query.market))
        df = ticker.financials
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()

    @staticmethod
    def transform_data(
        query: FundamentalsQueryParams, data: pd.DataFrame
    ) -> list[IncomeStatementData]:
        out: list[IncomeStatementData] = []
        for period, col in _iter_periods(data, query.limit):
            out.append(
                IncomeStatementData(
                    period_ending=period,
                    fiscal_year=period.year if period else None,
                    revenue=_pick(col, "Total Revenue", "Operating Revenue"),
                    cost_of_revenue=_pick(col, "Cost Of Revenue", "Reconciled Cost Of Revenue"),
                    gross_profit=_pick(col, "Gross Profit"),
                    operating_expense=_pick(col, "Operating Expense", "Total Operating Expenses"),
                    operating_income=_pick(col, "Operating Income", "Total Operating Income As Reported"),
                    ebitda=_pick(col, "EBITDA", "Normalized EBITDA"),
                    ebit=_pick(col, "EBIT"),
                    interest_expense=_pick(col, "Interest Expense", "Interest Expense Non Operating"),
                    pretax_income=_pick(col, "Pretax Income"),
                    income_tax_expense=_pick(col, "Tax Provision"),
                    net_income=_pick(col, "Net Income", "Net Income Common Stockholders"),
                    basic_eps=_pick(col, "Basic EPS"),
                    diluted_eps=_pick(col, "Diluted EPS"),
                    basic_shares=_pick(col, "Basic Average Shares"),
                    diluted_shares=_pick(col, "Diluted Average Shares"),
                )
            )
        return out


class YFinanceBalanceFetcher(Fetcher[FundamentalsQueryParams, list[BalanceSheetData]]):
    """资产负债表。"""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> FundamentalsQueryParams:
        return FundamentalsQueryParams(**params)

    @staticmethod
    def extract_data(query: FundamentalsQueryParams) -> pd.DataFrame:
        import yfinance as yf

        ticker = yf.Ticker(to_yf_symbol(query.symbol, query.market))
        df = ticker.balance_sheet
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()

    @staticmethod
    def transform_data(
        query: FundamentalsQueryParams, data: pd.DataFrame
    ) -> list[BalanceSheetData]:
        out: list[BalanceSheetData] = []
        for period, col in _iter_periods(data, query.limit):
            out.append(
                BalanceSheetData(
                    period_ending=period,
                    fiscal_year=period.year if period else None,
                    total_assets=_pick(col, "Total Assets"),
                    current_assets=_pick(col, "Current Assets", "Total Current Assets"),
                    cash_and_equivalents=_pick(
                        col, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"
                    ),
                    inventory=_pick(col, "Inventory"),
                    total_liabilities=_pick(
                        col, "Total Liabilities Net Minority Interest", "Total Liabilities"
                    ),
                    current_liabilities=_pick(col, "Current Liabilities", "Total Current Liabilities"),
                    total_debt=_pick(col, "Total Debt"),
                    long_term_debt=_pick(col, "Long Term Debt"),
                    total_equity=_pick(
                        col, "Stockholders Equity", "Total Equity Gross Minority Interest"
                    ),
                    retained_earnings=_pick(col, "Retained Earnings"),
                    shares_outstanding=_pick(col, "Ordinary Shares Number", "Share Issued"),
                )
            )
        return out


class YFinanceCashFlowFetcher(Fetcher[FundamentalsQueryParams, list[CashFlowData]]):
    """现金流量表。"""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> FundamentalsQueryParams:
        return FundamentalsQueryParams(**params)

    @staticmethod
    def extract_data(query: FundamentalsQueryParams) -> pd.DataFrame:
        import yfinance as yf

        ticker = yf.Ticker(to_yf_symbol(query.symbol, query.market))
        df = ticker.cashflow
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()

    @staticmethod
    def transform_data(
        query: FundamentalsQueryParams, data: pd.DataFrame
    ) -> list[CashFlowData]:
        out: list[CashFlowData] = []
        for period, col in _iter_periods(data, query.limit):
            out.append(
                CashFlowData(
                    period_ending=period,
                    fiscal_year=period.year if period else None,
                    operating_cash_flow=_pick(col, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities"),
                    capital_expenditure=_pick(col, "Capital Expenditure"),
                    free_cash_flow=_pick(col, "Free Cash Flow"),
                    investing_cash_flow=_pick(col, "Investing Cash Flow", "Cash Flow From Continuing Investing Activities"),
                    financing_cash_flow=_pick(col, "Financing Cash Flow", "Cash Flow From Continuing Financing Activities"),
                    dividends_paid=_pick(col, "Cash Dividends Paid", "Common Stock Dividend Paid"),
                    net_change_in_cash=_pick(col, "Changes In Cash", "End Cash Position"),
                )
            )
        return out


class YFinanceKeyMetricsFetcher(Fetcher[FundamentalsQueryParams, KeyMetricsData]):
    """关键指标 / 市值 / 估值。"""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> FundamentalsQueryParams:
        return FundamentalsQueryParams(**params)

    @staticmethod
    def extract_data(query: FundamentalsQueryParams) -> dict[str, Any]:
        import yfinance as yf

        ticker = yf.Ticker(to_yf_symbol(query.symbol, query.market))
        try:
            info = ticker.info
        except Exception:  # noqa: BLE001 — info 偶发抛错，降级为空
            info = {}
        return info if isinstance(info, dict) else {}

    @staticmethod
    def transform_data(
        query: FundamentalsQueryParams, data: dict[str, Any]
    ) -> KeyMetricsData:
        g = data.get
        # yfinance 新版 dividendYield 返回百分比(1.5)，旧版返回分数(0.015)。
        # 本模块统一为分数（前端/AkShare 路径按分数处理）。yield 分数不可能 >1。
        dy = _num(g("dividendYield"))
        if dy is not None and dy > 1:
            dy = dy / 100.0
        return KeyMetricsData(
            symbol=query.symbol.upper(),
            currency=g("currency"),
            price=_num(g("currentPrice") or g("regularMarketPrice")),
            market_cap=_num(g("marketCap")),
            enterprise_value=_num(g("enterpriseValue")),
            pe_ratio=_num(g("trailingPE")),
            forward_pe=_num(g("forwardPE")),
            peg_ratio=_num(g("trailingPegRatio") or g("pegRatio")),
            pb_ratio=_num(g("priceToBook")),
            ps_ratio=_num(g("priceToSalesTrailing12Months")),
            eps=_num(g("trailingEps")),
            forward_eps=_num(g("forwardEps")),
            book_value=_num(g("bookValue")),
            dividend_yield=dy,
            dividend_rate=_num(g("dividendRate")),
            beta=_num(g("beta")),
            shares_outstanding=_num(g("sharesOutstanding")),
            fifty_two_week_high=_num(g("fiftyTwoWeekHigh")),
            fifty_two_week_low=_num(g("fiftyTwoWeekLow")),
            name=g("shortName") or g("longName"),
        )
