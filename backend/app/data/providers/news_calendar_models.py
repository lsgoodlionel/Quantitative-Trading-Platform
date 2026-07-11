"""
标准化新闻 + 财报/分红日历数据模型（Wave-3f / A4）

设计参考: refs/OpenBB/.../provider/standard_models/
  {company_news, calendar_earnings, calendar_dividend}.py

原则:
  - 命名对齐 OpenBB 标准（英文 snake_case），前端做中文标签映射
  - 事件类字段尽量可空（不同市场/数据源覆盖度差异大，尽力填充）
  - 每类响应统一封装 {symbol, market, count, items/events, warnings}
"""

from __future__ import annotations

from datetime import date as DateType
from datetime import datetime

from pydantic import Field

from app.data.providers.base import Data, QueryParams

# ── 公司新闻 ──────────────────────────────────────────────────────


class NewsQueryParams(QueryParams):
    """公司新闻查询参数。"""

    symbol: str = Field(description="标的代码")
    market: str = Field(default="US", description="市场: US / HK / A")
    limit: int = Field(default=20, ge=1, le=100, description="返回最近 N 条")


class CompanyNewsItem(Data):
    """单条公司新闻（对齐 OpenBB CompanyNewsData）。"""

    published_at: datetime | None = Field(default=None, description="发布时间")
    title: str = Field(description="标题")
    publisher: str | None = Field(default=None, description="来源/发布方")
    author: str | None = Field(default=None, description="作者")
    summary: str | None = Field(default=None, description="摘要")
    url: str | None = Field(default=None, description="原文链接")
    thumbnail: str | None = Field(default=None, description="缩略图链接")
    symbols: list[str] = Field(default_factory=list, description="相关标的")


class CompanyNewsResponse(Data):
    """公司新闻流响应。"""

    symbol: str
    market: str
    count: int = 0
    items: list[CompanyNewsItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ── 财报日历 ──────────────────────────────────────────────────────


class CalendarQueryParams(QueryParams):
    """财报/分红日历查询参数（按标的）。"""

    symbol: str = Field(description="标的代码")
    market: str = Field(default="US", description="市场: US / HK / A")
    limit: int = Field(default=12, ge=1, le=40, description="返回最近/未来 N 期")


class EarningsEvent(Data):
    """单次财报事件（对齐 OpenBB CalendarEarningsData）。"""

    report_date: DateType | None = Field(default=None, description="财报披露日")
    symbol: str | None = Field(default=None, description="标的代码")
    name: str | None = Field(default=None, description="名称")
    period: str | None = Field(default=None, description="报告期，如 2024Q1/年报")
    eps_estimate: float | None = Field(default=None, description="EPS 一致预期")
    eps_actual: float | None = Field(default=None, description="实际 EPS")
    eps_previous: float | None = Field(default=None, description="上期同比 EPS")
    surprise_percent: float | None = Field(default=None, description="超预期百分比")
    is_upcoming: bool = Field(default=False, description="是否为未来事件")


class EarningsCalendarResponse(Data):
    """财报日历响应。"""

    symbol: str
    market: str
    count: int = 0
    events: list[EarningsEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ── 分红日历 ──────────────────────────────────────────────────────


class DividendEvent(Data):
    """单次分红事件（对齐 OpenBB CalendarDividendData）。"""

    ex_dividend_date: DateType | None = Field(default=None, description="除权除息日")
    symbol: str | None = Field(default=None, description="标的代码")
    name: str | None = Field(default=None, description="名称")
    amount: float | None = Field(default=None, description="每股分红金额")
    record_date: DateType | None = Field(default=None, description="股权登记日")
    payment_date: DateType | None = Field(default=None, description="派息日")
    declaration_date: DateType | None = Field(default=None, description="公告/预案日")
    dividend_yield: float | None = Field(default=None, description="股息率（分数）")
    period: str | None = Field(default=None, description="对应报告期")
    is_upcoming: bool = Field(default=False, description="是否为未来事件")


class DividendCalendarResponse(Data):
    """分红日历响应。"""

    symbol: str
    market: str
    count: int = 0
    events: list[DividendEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
