"""
交易日历基类（K7）

定位：日历是**校验与对齐层**，不是驱动层。回测仍按 bar 序列迭代，日历只负责
  1. 校验 bar 是否落在交易日
  2. 找出日历有、数据没有的缺口
  3. 提供年化基准（实测交易日数，替代 252/245/242 常数）

不引入 `exchange_calendars` 等重依赖：假日表见 `holidays/`。

参考 zipline `zipline/utils/calendar_utils.py` 的接口形态（Apache-2.0），
本文件为独立实现。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, time, timedelta

from app.data.models import Market

logger = logging.getLogger(__name__)

# 一年的平均日历天数（含闰年），用于把区间实测交易日折算成年化基准
DAYS_PER_YEAR = 365.25
# 区间太短时实测年化基准噪声过大，直接回退到该市场的默认常数
MIN_SPAN_DAYS_FOR_ESTIMATE = 90
# `next_session` / `previous_session` 的最大搜索跨度（长假 + 极端休市的安全上界）
MAX_SESSION_SEARCH_DAYS = 30
# 年化基准的合理区间：超出说明数据或假日表有问题，拒绝采信
MIN_SESSIONS_PER_YEAR, MAX_SESSIONS_PER_YEAR = 180, 260

SATURDAY = 5


@dataclass(frozen=True)
class SessionWindow:
    """一段连续交易时段（交易所本地时间）。午休即表现为两段不相连的窗口。"""

    start: time
    end: time

    def contains(self, moment: time) -> bool:
        return self.start <= moment < self.end


class TradingCalendar(ABC):
    """
    单市场交易日历。

    子类需提供：
      - `name` / `market` / `default_sessions_per_year`
      - `_year_holidays(year)`：该年休市日；返回 None 表示该年超出静态表覆盖
      - `session_windows(day)`：该交易日的交易时段（含午休拆分）
    """

    name: str
    market: Market
    default_sessions_per_year: int

    def __init__(self) -> None:
        # 覆盖区间外只对每个年份告警一次，避免逐 bar 刷屏
        self._warned_years: set[int] = set()

    # ── 子类实现 ──────────────────────────────────────────────

    @abstractmethod
    def _year_holidays(self, year: int) -> frozenset[Date] | None:
        """该年休市日集合；None 表示静态假日表未覆盖该年。"""
        ...

    @abstractmethod
    def session_windows(self, day: Date) -> tuple[SessionWindow, ...]:
        """该交易日的交易时段；非交易日返回空元组。"""
        ...

    # ── 交易日判定 ────────────────────────────────────────────

    def is_session(self, day: Date) -> bool:
        """是否为交易日（排除周末与假日）。"""
        day = _as_date(day)
        if day.weekday() >= SATURDAY:
            return False
        holidays = self._year_holidays(day.year)
        if holidays is None:
            self._warn_uncovered(day.year)
            return True   # 降级：只排除周末
        return day not in holidays

    def _warn_uncovered(self, year: int) -> None:
        if year in self._warned_years:
            return
        self._warned_years.add(year)
        logger.warning(
            "%s 假日表未覆盖 %d 年，该年仅按「排除周末」判定交易日，结果可能偏多。",
            self.name, year,
        )

    def sessions_in_range(self, start: Date, end: Date) -> list[Date]:
        """闭区间 [start, end] 内的全部交易日。"""
        start, end = _as_date(start), _as_date(end)
        if end < start:
            raise ValueError(f"end {end} 早于 start {start}")
        days: list[Date] = []
        cursor = start
        while cursor <= end:
            if self.is_session(cursor):
                days.append(cursor)
            cursor += timedelta(days=1)
        return days

    def sessions_count(self, start: Date, end: Date) -> int:
        return len(self.sessions_in_range(start, end))

    def next_session(self, day: Date) -> Date:
        """`day` 之后的第一个交易日（严格大于）。"""
        return self._search_session(day, step=1)

    def previous_session(self, day: Date) -> Date:
        """`day` 之前的最后一个交易日（严格小于）。"""
        return self._search_session(day, step=-1)

    def _search_session(self, day: Date, step: int) -> Date:
        cursor = _as_date(day)
        for _ in range(MAX_SESSION_SEARCH_DAYS):
            cursor += timedelta(days=step)
            if self.is_session(cursor):
                return cursor
        raise ValueError(
            f"{self.name}: 自 {day} 起连续 {MAX_SESSION_SEARCH_DAYS} 天无交易日，假日表可能有误"
        )

    # ── 盘中时段 ──────────────────────────────────────────────

    def is_trading_time(self, moment: datetime) -> bool:
        """时刻是否落在交易时段内（午休、盘前盘后均为 False）。"""
        windows = self.session_windows(moment.date())
        return any(w.contains(moment.time()) for w in windows)

    # ── 校验与对齐 ────────────────────────────────────────────

    def non_sessions(self, days: Iterable[Date]) -> list[Date]:
        """挑出不属于交易日的日期（数据源脏数据检出）。"""
        return sorted({d for d in (_as_date(x) for x in days) if not self.is_session(d)})

    def missing_sessions(self, days: Iterable[Date], start: Date, end: Date) -> list[Date]:
        """日历中有、给定序列中缺失的交易日（数据缺口检出）。"""
        observed = {_as_date(d) for d in days}
        return [d for d in self.sessions_in_range(start, end) if d not in observed]

    def sessions_per_year(self, start: Date, end: Date) -> float:
        """
        区间实测的年化交易日基准。

        区间短于 `MIN_SPAN_DAYS_FOR_ESTIMATE` 或折算结果离谱时回退默认常数 ——
        年化指标对该值敏感，宁可用保守常数也不用噪声估计。
        """
        start, end = _as_date(start), _as_date(end)
        span_days = (end - start).days + 1
        if span_days < MIN_SPAN_DAYS_FOR_ESTIMATE:
            return float(self.default_sessions_per_year)

        estimate = self.sessions_count(start, end) * DAYS_PER_YEAR / span_days
        if not MIN_SESSIONS_PER_YEAR <= estimate <= MAX_SESSIONS_PER_YEAR:
            logger.warning(
                "%s: 区间 %s~%s 折算年化交易日 %.1f 超出合理范围，回退默认值 %d",
                self.name, start, end, estimate, self.default_sessions_per_year,
            )
            return float(self.default_sessions_per_year)
        return estimate


def _as_date(value: Date | datetime) -> Date:
    """统一收敛到 `date`：datetime / date 都可传入。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, Date):
        return value
    raise TypeError(f"需要 date 或 datetime，收到 {type(value).__name__}")
