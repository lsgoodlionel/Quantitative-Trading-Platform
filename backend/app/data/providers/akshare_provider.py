"""
AkShare A 股基本面数据提供者（Wave-2a / A2）

数据来源:
  - ak.stock_financial_report_sina(stock, symbol=报表名)  → 三大报表（新浪，按报告期）
  - ak.stock_a_indicator_lg(symbol)                       → PE/PB/PS/股息率/总市值

A 股接口不稳定且字段中文，全部 best-effort：单节失败返回空并记 warning，不硬失败。
财务比率不在此层取，由 service 层从三大报表统一派生（DRY，跨市场一致）。
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

_SH_PREFIXES = ("6",)


def to_ak_symbol(symbol: str) -> str:
    """外部代码 → akshare 'sh600519' / 'sz000001' 格式。"""
    s = symbol.upper().strip()
    if s.startswith("SH"):
        return "sh" + s[2:]
    if s.startswith("SZ"):
        return "sz" + s[2:]
    return ("sh" if s[:1] in _SH_PREFIXES else "sz") + s


def _bare_code(symbol: str) -> str:
    """外部代码 → 纯数字代码（stock_a_indicator_lg 需要）。"""
    s = symbol.upper().strip()
    return s[2:] if s[:2] in ("SH", "SZ") else s


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _row_val(row: pd.Series, *labels: str) -> float | None:
    """按中文科目名依次从一行取数值。"""
    for label in labels:
        if label in row.index:
            val = _num(row.get(label))
            if val is not None:
                return val
    return None


def _report_date(row: pd.Series) -> DateType | None:
    for key in ("报告日", "报表日期", "日期"):
        raw = row.get(key) if key in row.index else None
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            continue
        try:
            ts = pd.Timestamp(str(raw))
        except (ValueError, TypeError):
            continue
        if not pd.isna(ts):
            return ts.date()
    return None


def _fetch_sina_report(query: FundamentalsQueryParams, report: str) -> pd.DataFrame:
    """拉取新浪某张报表；失败返回空表。"""
    import akshare as ak

    try:
        df = ak.stock_financial_report_sina(stock=to_ak_symbol(query.symbol), symbol=report)
    except Exception as e:  # noqa: BLE001
        logger.warning("akshare sina report failed", report=report, error=str(e))
        return pd.DataFrame()
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()


class AkShareIncomeFetcher(Fetcher[FundamentalsQueryParams, list[IncomeStatementData]]):
    """A 股利润表。"""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> FundamentalsQueryParams:
        return FundamentalsQueryParams(**params)

    @staticmethod
    def extract_data(query: FundamentalsQueryParams) -> pd.DataFrame:
        return _fetch_sina_report(query, "利润表")

    @staticmethod
    def transform_data(
        query: FundamentalsQueryParams, data: pd.DataFrame
    ) -> list[IncomeStatementData]:
        out: list[IncomeStatementData] = []
        for _, row in data.head(query.limit).iterrows():
            period = _report_date(row)
            out.append(
                IncomeStatementData(
                    period_ending=period,
                    fiscal_year=period.year if period else None,
                    currency="CNY",
                    revenue=_row_val(row, "营业总收入", "营业收入"),
                    cost_of_revenue=_row_val(row, "营业总成本", "营业成本"),
                    operating_income=_row_val(row, "营业利润"),
                    pretax_income=_row_val(row, "利润总额"),
                    income_tax_expense=_row_val(row, "所得税费用"),
                    net_income=_row_val(row, "净利润"),
                    basic_eps=_row_val(row, "基本每股收益"),
                    diluted_eps=_row_val(row, "稀释每股收益"),
                )
            )
        return out


class AkShareBalanceFetcher(Fetcher[FundamentalsQueryParams, list[BalanceSheetData]]):
    """A 股资产负债表。"""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> FundamentalsQueryParams:
        return FundamentalsQueryParams(**params)

    @staticmethod
    def extract_data(query: FundamentalsQueryParams) -> pd.DataFrame:
        return _fetch_sina_report(query, "资产负债表")

    @staticmethod
    def transform_data(
        query: FundamentalsQueryParams, data: pd.DataFrame
    ) -> list[BalanceSheetData]:
        out: list[BalanceSheetData] = []
        for _, row in data.head(query.limit).iterrows():
            period = _report_date(row)
            out.append(
                BalanceSheetData(
                    period_ending=period,
                    fiscal_year=period.year if period else None,
                    currency="CNY",
                    total_assets=_row_val(row, "资产总计", "资产合计"),
                    current_assets=_row_val(row, "流动资产合计"),
                    cash_and_equivalents=_row_val(row, "货币资金"),
                    inventory=_row_val(row, "存货"),
                    total_liabilities=_row_val(row, "负债合计"),
                    current_liabilities=_row_val(row, "流动负债合计"),
                    long_term_debt=_row_val(row, "长期借款"),
                    total_equity=_row_val(
                        row, "所有者权益(或股东权益)合计", "股东权益合计", "所有者权益合计"
                    ),
                    retained_earnings=_row_val(row, "未分配利润"),
                )
            )
        return out


class AkShareCashFlowFetcher(Fetcher[FundamentalsQueryParams, list[CashFlowData]]):
    """A 股现金流量表。"""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> FundamentalsQueryParams:
        return FundamentalsQueryParams(**params)

    @staticmethod
    def extract_data(query: FundamentalsQueryParams) -> pd.DataFrame:
        return _fetch_sina_report(query, "现金流量表")

    @staticmethod
    def transform_data(
        query: FundamentalsQueryParams, data: pd.DataFrame
    ) -> list[CashFlowData]:
        out: list[CashFlowData] = []
        for _, row in data.head(query.limit).iterrows():
            period = _report_date(row)
            out.append(
                CashFlowData(
                    period_ending=period,
                    fiscal_year=period.year if period else None,
                    currency="CNY",
                    operating_cash_flow=_row_val(row, "经营活动产生的现金流量净额"),
                    capital_expenditure=_row_val(
                        row, "购建固定资产、无形资产和其他长期资产支付的现金"
                    ),
                    investing_cash_flow=_row_val(row, "投资活动产生的现金流量净额"),
                    financing_cash_flow=_row_val(row, "筹资活动产生的现金流量净额"),
                    dividends_paid=_row_val(row, "分配股利、利润或偿付利息支付的现金"),
                    net_change_in_cash=_row_val(row, "现金及现金等价物净增加额"),
                )
            )
        return out


class AkShareKeyMetricsFetcher(Fetcher[FundamentalsQueryParams, KeyMetricsData]):
    """A 股估值 / 市值（百度股市通估值指标，逐指标 best-effort）。

    注：旧接口 stock_a_indicator_lg 在 akshare 新版已移除，改用
    stock_zh_valuation_baidu 按指标取最新值；单指标失败不影响其他指标。
    """

    # 指标名 → KeyMetricsData 字段（总市值单位为亿元）
    _INDICATORS = {
        "总市值": "market_cap",
        "市盈率(TTM)": "pe_ratio",
        "市净率": "pb_ratio",
        "市销率": "ps_ratio",
    }

    @staticmethod
    def transform_query(params: dict[str, Any]) -> FundamentalsQueryParams:
        return FundamentalsQueryParams(**params)

    @staticmethod
    def extract_data(query: FundamentalsQueryParams) -> dict[str, float]:
        import akshare as ak

        code = _bare_code(query.symbol)
        out: dict[str, float] = {}
        for indicator in AkShareKeyMetricsFetcher._INDICATORS:
            try:
                df = ak.stock_zh_valuation_baidu(
                    symbol=code, indicator=indicator, period="近一年"
                )
                if isinstance(df, pd.DataFrame) and not df.empty:
                    val = df.iloc[-1].get("value")
                    if val is not None and pd.notna(val):
                        out[indicator] = float(val)
            except Exception as e:  # noqa: BLE001
                logger.debug("baidu valuation failed", indicator=indicator, error=str(e))
        return out

    @staticmethod
    def transform_data(
        query: FundamentalsQueryParams, data: dict[str, float]
    ) -> KeyMetricsData:
        base = KeyMetricsData(symbol=query.symbol.upper(), currency="CNY")
        if not data:
            return base
        update: dict[str, float] = {}
        for indicator, field in AkShareKeyMetricsFetcher._INDICATORS.items():
            if indicator not in data:
                continue
            val = data[indicator]
            # 总市值单位为亿元 → 元
            update[field] = val * 1e8 if field == "market_cap" else val
        return base.model_copy(update=update)
