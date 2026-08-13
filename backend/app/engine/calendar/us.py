"""
美股（NYSE / NASDAQ）交易日历（K7）

常规时段 09:30–16:00（美东），无午休。
提前收市日 13:00 收盘（感恩节次日 / 独立日前夕 / 平安夜）。
"""

from __future__ import annotations

from datetime import date as Date
from datetime import time

from app.data.models import Market
from app.engine.backtest.metrics import TRADING_DAYS_US
from app.engine.calendar.base import SessionWindow, TradingCalendar
from app.engine.calendar.holidays import us as us_holidays

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

_REGULAR_WINDOWS = (SessionWindow(REGULAR_OPEN, REGULAR_CLOSE),)
_EARLY_WINDOWS = (SessionWindow(REGULAR_OPEN, EARLY_CLOSE),)


class USCalendar(TradingCalendar):
    """NYSE 交易日历。假日由规则推导，任意年份可用。"""

    name = "NYSE"
    market = Market.US
    default_sessions_per_year = TRADING_DAYS_US

    def __init__(self) -> None:
        super().__init__()
        self._cache: dict[int, frozenset[Date]] = {}
        self._early_cache: dict[int, frozenset[Date]] = {}

    def _year_holidays(self, year: int) -> frozenset[Date]:
        if year not in self._cache:
            self._cache[year] = us_holidays.holidays(year)
        return self._cache[year]

    def is_early_close(self, day: Date) -> bool:
        """是否为提前收市日（13:00 收盘）。"""
        if not self.is_session(day):
            return False
        if day.year not in self._early_cache:
            self._early_cache[day.year] = us_holidays.early_closes(day.year)
        return day in self._early_cache[day.year]

    def session_windows(self, day: Date) -> tuple[SessionWindow, ...]:
        if not self.is_session(day):
            return ()
        return _EARLY_WINDOWS if self.is_early_close(day) else _REGULAR_WINDOWS
