"""
基本面数据服务（Wave-2a / A2）

职责:
  1. 按市场路由到对应 provider（US/HK → yfinance，A → akshare）
  2. 并发拉取 income / balance / cashflow / metrics 四节
  3. 从三大报表统一派生财务比率（跨市场一致，DRY）
  4. 单节失败降级为 warning，不影响其它节（尽力覆盖）

对外仅暴露 FundamentalsService.get_fundamentals → FundamentalsBundle。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger
from app.data.providers import akshare_provider as ak_p
from app.data.providers import yfinance_provider as yf_p
from app.data.providers.base import Fetcher
from app.data.providers.models import (
    BalanceSheetData,
    FinancialRatiosData,
    FundamentalsBundle,
    IncomeStatementData,
    KeyMetricsData,
)

logger = get_logger(__name__)

_SUPPORTED_MARKETS = {"US", "HK", "A"}


def _fetchers_for(market: str) -> dict[str, type[Fetcher]]:
    """返回该市场的 {income,balance,cashflow,metrics} Fetcher 类映射。"""
    if market == "A":
        return {
            "income": ak_p.AkShareIncomeFetcher,
            "balance": ak_p.AkShareBalanceFetcher,
            "cashflow": ak_p.AkShareCashFlowFetcher,
            "metrics": ak_p.AkShareKeyMetricsFetcher,
        }
    # US / HK
    return {
        "income": yf_p.YFinanceIncomeFetcher,
        "balance": yf_p.YFinanceBalanceFetcher,
        "cashflow": yf_p.YFinanceCashFlowFetcher,
        "metrics": yf_p.YFinanceKeyMetricsFetcher,
    }


def _ratio(numer: float | None, denom: float | None) -> float | None:
    """安全比值；分母为 0 / None 返回 None。"""
    if numer is None or denom is None or denom == 0:
        return None
    return numer / denom


def _derive_ratios(
    income: list[IncomeStatementData],
    balance: list[BalanceSheetData],
) -> list[FinancialRatiosData]:
    """从利润表 + 资产负债表按报告期匹配派生财务比率。

    按精确 period_ending 匹配（而非 fiscal_year）——A 股季度数据同年多期，
    按年匹配会把不同季度的利润表错配到同一份资产负债表。缺 period_ending
    时回退到 fiscal_year（年报单期场景，如 yfinance）。
    """
    bal_by_period = {b.period_ending: b for b in balance if b.period_ending}
    bal_by_year = {b.fiscal_year: b for b in balance if b.fiscal_year is not None}
    ratios: list[FinancialRatiosData] = []
    for inc in income:
        bal = None
        if inc.period_ending and inc.period_ending in bal_by_period:
            bal = bal_by_period[inc.period_ending]
        elif inc.fiscal_year is not None:
            bal = bal_by_year.get(inc.fiscal_year)
        rev = inc.revenue
        gross = inc.gross_profit
        if gross is None and rev is not None and inc.cost_of_revenue is not None:
            gross = rev - inc.cost_of_revenue
        row = FinancialRatiosData(
            period_ending=inc.period_ending,
            fiscal_year=inc.fiscal_year,
            fiscal_period=inc.fiscal_period,
            gross_margin=_ratio(gross, rev),
            operating_margin=_ratio(inc.operating_income, rev),
            net_margin=_ratio(inc.net_income, rev),
        )
        if bal is not None:
            row = row.model_copy(update=_balance_ratios(inc, bal))
        ratios.append(row)
    return ratios


def _balance_ratios(inc: IncomeStatementData, bal: BalanceSheetData) -> dict[str, Any]:
    """涉及资产负债表的比率。"""
    quick_assets = None
    if bal.current_assets is not None:
        quick_assets = bal.current_assets - (bal.inventory or 0.0)
    return {
        "current_ratio": _ratio(bal.current_assets, bal.current_liabilities),
        "quick_ratio": _ratio(quick_assets, bal.current_liabilities),
        "debt_to_equity": _ratio(bal.total_liabilities, bal.total_equity),
        "return_on_assets": _ratio(inc.net_income, bal.total_assets),
        "return_on_equity": _ratio(inc.net_income, bal.total_equity),
        "asset_turnover": _ratio(inc.revenue, bal.total_assets),
    }


class FundamentalsService:
    """基本面数据统一入口。无状态，可直接实例化。"""

    async def get_fundamentals(
        self,
        symbol: str,
        market: str = "US",
        limit: int = 5,
    ) -> FundamentalsBundle:
        market = market.upper()
        if market not in _SUPPORTED_MARKETS:
            raise ValueError(f"不支持的市场: {market}（可选 US/HK/A）")
        symbol = symbol.strip()
        if not symbol:
            raise ValueError("标的代码不能为空")

        fetchers = _fetchers_for(market)
        params = {"symbol": symbol, "market": market, "limit": limit}
        keys = ["income", "balance", "cashflow", "metrics"]
        results = await asyncio.gather(
            *(fetchers[k].fetch_data(params) for k in keys),
            return_exceptions=True,
        )

        bundle_parts: dict[str, Any] = {}
        warnings: list[str] = []
        for key, res in zip(keys, results):
            if isinstance(res, Exception):
                logger.warning("fundamentals section failed", section=key, error=str(res))
                warnings.append(f"{key} 数据获取失败: {res}")
                bundle_parts[key] = None if key == "metrics" else []
            else:
                bundle_parts[key] = res

        income = bundle_parts["income"] or []
        balance = bundle_parts["balance"] or []
        metrics: KeyMetricsData | None = bundle_parts["metrics"]

        return FundamentalsBundle(
            symbol=symbol.upper(),
            market=market,
            currency=_infer_currency(market, metrics),
            name=getattr(metrics, "name", None) if metrics else None,
            income=income,
            balance=balance,
            cashflow=bundle_parts["cashflow"] or [],
            ratios=_derive_ratios(income, balance),
            metrics=metrics,
            warnings=warnings,
        )


def _infer_currency(market: str, metrics: KeyMetricsData | None) -> str | None:
    if metrics and metrics.currency:
        return metrics.currency
    return {"US": "USD", "HK": "HKD", "A": "CNY"}.get(market)
