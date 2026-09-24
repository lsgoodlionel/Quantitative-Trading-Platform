"""
港股（HKEX）交易日历（K7）

连续竞价时段：上午 09:30–12:00，下午 13:00–16:00 —— 午休 12:00–13:00。

⚠️ 契约 waveKb §2.2 把港股午休写成「13:00–14:00」，与港交所实际时段不符
（港交所午市 13:00 开市、16:00 收市）。此处按**实际交易时段**实现，
否则盘中时段判定会把整个下午的第一小时误判为休市。
"""

from __future__ import annotations

from datetime import date as Date
from datetime import time

from app.data.models import Market
from app.engine.backtest.metrics import TRADING_DAYS_HK
from app.engine.calendar.base import SessionWindow, TradingCalendar
from app.engine.calendar.holidays import hk as hk_holidays

MORNING_OPEN = time(9, 30)
LUNCH_START = time(12, 0)
LUNCH_END = time(13, 0)
AFTERNOON_CLOSE = time(16, 0)

_WINDOWS = (
    SessionWindow(MORNING_OPEN, LUNCH_START),
    SessionWindow(LUNCH_END, AFTERNOON_CLOSE),
)


class HKCalendar(TradingCalendar):
    """港交所交易日历。假日为静态表，覆盖区间见 `holidays.hk.COVERAGE_YEARS`。"""

    name = "HKEX"
    market = Market.HK
    default_sessions_per_year = TRADING_DAYS_HK
    coverage_years = hk_holidays.COVERAGE_YEARS

    def _year_holidays(self, year: int) -> frozenset[Date] | None:
        return hk_holidays.holidays(year)

    def session_windows(self, day: Date) -> tuple[SessionWindow, ...]:
        return _WINDOWS if self.is_session(day) else ()
