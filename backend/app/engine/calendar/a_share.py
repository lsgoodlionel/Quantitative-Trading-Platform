"""
A 股（上交所 / 深交所）交易日历（K7）

连续竞价时段：上午 09:30–11:30，下午 13:00–15:00 —— 午休 11:30–13:00。
（集合竞价 09:15–09:25 与收盘集合竞价不计入连续交易时段。）
"""

from __future__ import annotations

from datetime import date as Date
from datetime import time

from app.data.models import Market
from app.engine.backtest.metrics import TRADING_DAYS_A
from app.engine.calendar.base import SessionWindow, TradingCalendar
from app.engine.calendar.holidays import a_share as a_holidays

MORNING_OPEN = time(9, 30)
LUNCH_START = time(11, 30)
LUNCH_END = time(13, 0)
AFTERNOON_CLOSE = time(15, 0)

_WINDOWS = (
    SessionWindow(MORNING_OPEN, LUNCH_START),
    SessionWindow(LUNCH_END, AFTERNOON_CLOSE),
)


class AShareCalendar(TradingCalendar):
    """沪深交易日历。假日为静态表，覆盖区间见 `holidays.a_share.COVERAGE_YEARS`。"""

    name = "SSE"
    market = Market.A
    default_sessions_per_year = TRADING_DAYS_A
    coverage_years = a_holidays.COVERAGE_YEARS

    def _year_holidays(self, year: int) -> frozenset[Date] | None:
        return a_holidays.holidays(year)

    def session_windows(self, day: Date) -> tuple[SessionWindow, ...]:
        return _WINDOWS if self.is_session(day) else ()
