"""
标准化基本面数据模型（Wave-2a / A2）

设计参考: refs/OpenBB/.../provider/standard_models/
  {income_statement, balance_sheet, cash_flow, financial_ratios, key_metrics}.py

原则:
  - 所有财务数值字段可空（不同市场/数据源覆盖度差异大，尽力填充）
  - 报告期字段统一: period_ending / fiscal_year / fiscal_period / currency
  - 命名对齐 OpenBB 标准（英文 snake_case），前端做中文标签映射
  - 金额单位: 报表原始货币单位（不做换算），currency 字段标识
"""

from __future__ import annotations

from datetime import date as DateType

from pydantic import Field

from app.data.providers.base import Data, QueryParams


class FundamentalsQueryParams(QueryParams):
    """基本面查询参数（各报表 Fetcher 共用）。"""

    symbol: str = Field(description="标的代码")
    market: str = Field(default="US", description="市场: US / HK / A")
    limit: int = Field(default=5, ge=1, le=20, description="返回最近 N 期")


class _PeriodData(Data):
    """报告期公共字段。"""

    period_ending: DateType | None = Field(default=None, description="报告期结束日")
    fiscal_year: int | None = Field(default=None, description="财年")
    fiscal_period: str | None = Field(default=None, description="财报周期，如 FY/Q1")
    currency: str | None = Field(default=None, description="报表货币")


class IncomeStatementData(_PeriodData):
    """利润表。"""

    revenue: float | None = Field(default=None, description="营业总收入")
    cost_of_revenue: float | None = Field(default=None, description="营业成本")
    gross_profit: float | None = Field(default=None, description="毛利润")
    operating_expense: float | None = Field(default=None, description="营业费用")
    operating_income: float | None = Field(default=None, description="营业利润")
    ebitda: float | None = Field(default=None, description="EBITDA")
    ebit: float | None = Field(default=None, description="EBIT")
    interest_expense: float | None = Field(default=None, description="利息费用")
    pretax_income: float | None = Field(default=None, description="税前利润")
    income_tax_expense: float | None = Field(default=None, description="所得税")
    net_income: float | None = Field(default=None, description="净利润")
    basic_eps: float | None = Field(default=None, description="基本每股收益")
    diluted_eps: float | None = Field(default=None, description="稀释每股收益")
    basic_shares: float | None = Field(default=None, description="基本加权股本")
    diluted_shares: float | None = Field(default=None, description="稀释加权股本")


class BalanceSheetData(_PeriodData):
    """资产负债表。"""

    total_assets: float | None = Field(default=None, description="总资产")
    current_assets: float | None = Field(default=None, description="流动资产")
    cash_and_equivalents: float | None = Field(default=None, description="现金及等价物")
    inventory: float | None = Field(default=None, description="存货")
    total_liabilities: float | None = Field(default=None, description="总负债")
    current_liabilities: float | None = Field(default=None, description="流动负债")
    total_debt: float | None = Field(default=None, description="总债务")
    long_term_debt: float | None = Field(default=None, description="长期债务")
    total_equity: float | None = Field(default=None, description="股东权益")
    retained_earnings: float | None = Field(default=None, description="留存收益")
    shares_outstanding: float | None = Field(default=None, description="流通股本")


class CashFlowData(_PeriodData):
    """现金流量表。"""

    operating_cash_flow: float | None = Field(default=None, description="经营活动现金流")
    capital_expenditure: float | None = Field(default=None, description="资本支出")
    free_cash_flow: float | None = Field(default=None, description="自由现金流")
    investing_cash_flow: float | None = Field(default=None, description="投资活动现金流")
    financing_cash_flow: float | None = Field(default=None, description="筹资活动现金流")
    dividends_paid: float | None = Field(default=None, description="分红支出")
    net_change_in_cash: float | None = Field(default=None, description="现金净变动")


class FinancialRatiosData(_PeriodData):
    """财务比率（部分派生自三大报表）。"""

    current_ratio: float | None = Field(default=None, description="流动比率")
    quick_ratio: float | None = Field(default=None, description="速动比率")
    debt_to_equity: float | None = Field(default=None, description="产权比率(负债/权益)")
    gross_margin: float | None = Field(default=None, description="毛利率")
    operating_margin: float | None = Field(default=None, description="营业利润率")
    net_margin: float | None = Field(default=None, description="净利率")
    return_on_assets: float | None = Field(default=None, description="总资产收益率 ROA")
    return_on_equity: float | None = Field(default=None, description="净资产收益率 ROE")
    asset_turnover: float | None = Field(default=None, description="资产周转率")


class KeyMetricsData(Data):
    """关键指标 / 市值 / 估值（快照，非报告期）。"""

    symbol: str | None = Field(default=None, description="标的代码")
    currency: str | None = Field(default=None, description="货币")
    price: float | None = Field(default=None, description="最新价")
    market_cap: float | None = Field(default=None, description="市值")
    enterprise_value: float | None = Field(default=None, description="企业价值 EV")
    pe_ratio: float | None = Field(default=None, description="市盈率 TTM")
    forward_pe: float | None = Field(default=None, description="预期市盈率")
    peg_ratio: float | None = Field(default=None, description="PEG")
    pb_ratio: float | None = Field(default=None, description="市净率")
    ps_ratio: float | None = Field(default=None, description="市销率")
    eps: float | None = Field(default=None, description="每股收益 TTM")
    forward_eps: float | None = Field(default=None, description="预期每股收益")
    book_value: float | None = Field(default=None, description="每股净资产")
    dividend_yield: float | None = Field(default=None, description="股息率")
    dividend_rate: float | None = Field(default=None, description="每股股息")
    beta: float | None = Field(default=None, description="贝塔")
    shares_outstanding: float | None = Field(default=None, description="总股本")
    fifty_two_week_high: float | None = Field(default=None, description="52 周最高")
    fifty_two_week_low: float | None = Field(default=None, description="52 周最低")


class FundamentalsBundle(Data):
    """基本面聚合响应（一次返回全部分节）。"""

    symbol: str
    market: str
    currency: str | None = None
    name: str | None = None
    income: list[IncomeStatementData] = Field(default_factory=list)
    balance: list[BalanceSheetData] = Field(default_factory=list)
    cashflow: list[CashFlowData] = Field(default_factory=list)
    ratios: list[FinancialRatiosData] = Field(default_factory=list)
    metrics: KeyMetricsData | None = None
    warnings: list[str] = Field(default_factory=list)
