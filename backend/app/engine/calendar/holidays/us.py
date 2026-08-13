"""
NYSE 假日表 —— 规则推导 + 特殊休市静态表（K7）

NYSE 的常规假日全部由规则确定（第 N 个星期几 / 固定日期 + 周末顺延），
因此用规则生成而非静态表：任意年份都可推导，不受静态表覆盖区间限制。
无法用规则表达的临时休市（国葬、飓风）走 `SPECIAL_CLOSURES` 静态表。

周末顺延规则（NYSE 惯例）：
  - 假日落在周六 → 提前到前一个周五
  - 假日落在周日 → 顺延到下一个周一
  - **元旦例外**：1 月 1 日落在周六时不补假（前一年 12 月 31 日照常开市）

已对 2022–2026 逐日核对（见 tests/test_calendar.py）。
"""

from __future__ import annotations

from datetime import date as Date
from datetime import timedelta

# 马丁·路德·金纪念日自 1998 年起成为 NYSE 假日
MLK_FIRST_YEAR = 1998
# 六月节（Juneteenth）自 2022 年起成为 NYSE 假日
JUNETEENTH_FIRST_YEAR = 2022

MONDAY, THURSDAY, FRIDAY, SATURDAY, SUNDAY = 0, 3, 4, 5, 6

# 无法用规则表达的临时休市（国葬 / 自然灾害 / 系统性事件）
SPECIAL_CLOSURES: frozenset[Date] = frozenset(
    {
        Date(2001, 9, 11), Date(2001, 9, 12),   # 9·11 事件
        Date(2001, 9, 13), Date(2001, 9, 14),
        Date(2004, 6, 11),                      # 里根国葬
        Date(2007, 1, 2),                       # 福特国葬
        Date(2012, 10, 29), Date(2012, 10, 30),  # 飓风桑迪
        Date(2018, 12, 5),                      # 老布什国葬
        Date(2025, 1, 9),                       # 卡特国葬
    }
)

# 提前收市日（美东 13:00 收盘）——由规则推导，见 `early_closes()`
EARLY_CLOSE_TIME_HOUR = 13


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> Date:
    """当月第 n 个 weekday（n 从 1 开始）。"""
    first = Date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> Date:
    """当月最后一个 weekday。"""
    next_month = Date(year + (month == 12), month % 12 + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def easter_sunday(year: int) -> Date:
    """复活节主日（匿名格里高利算法）。耶稣受难日 = 复活节前的星期五。"""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lam = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lam) // 451
    month, day = divmod(h + lam - 7 * m + 114, 31)
    return Date(year, month, day + 1)


def _observed(day: Date, *, shift_saturday: bool = True) -> Date | None:
    """周末顺延。shift_saturday=False 时周六假日直接取消（元旦规则）。"""
    if day.weekday() == SATURDAY:
        return day - timedelta(days=1) if shift_saturday else None
    if day.weekday() == SUNDAY:
        return day + timedelta(days=1)
    return day


def holidays(year: int) -> frozenset[Date]:
    """给定年份的 NYSE 全休市日（不含周末）。"""
    days: list[Date | None] = [
        _observed(Date(year, 1, 1), shift_saturday=False),   # 元旦
        _nth_weekday(year, 2, MONDAY, 3),                    # 华盛顿诞辰
        easter_sunday(year) - timedelta(days=2),             # 耶稣受难日
        _last_weekday(year, 5, MONDAY),                      # 阵亡将士纪念日
        _observed(Date(year, 7, 4)),                         # 独立日
        _nth_weekday(year, 9, MONDAY, 1),                    # 劳动节
        _nth_weekday(year, 11, THURSDAY, 4),                 # 感恩节
        _observed(Date(year, 12, 25)),                       # 圣诞节
    ]
    if year >= MLK_FIRST_YEAR:
        days.append(_nth_weekday(year, 1, MONDAY, 3))
    if year >= JUNETEENTH_FIRST_YEAR:
        days.append(_observed(Date(year, 6, 19)))

    resolved = {d for d in days if d is not None and d.year == year}
    resolved |= {d for d in SPECIAL_CLOSURES if d.year == year}
    return frozenset(resolved)


def early_closes(year: int) -> frozenset[Date]:
    """
    提前收市日（13:00 收盘）：
      - 感恩节次日
      - 7 月 3 日（当它是工作日且 7 月 4 日照常休市）
      - 12 月 24 日（当它是工作日且 12 月 25 日照常休市）
    """
    holiday_set = holidays(year)
    result = {_nth_weekday(year, 11, THURSDAY, 4) + timedelta(days=1)}

    for eve, holiday in ((Date(year, 7, 3), Date(year, 7, 4)),
                         (Date(year, 12, 24), Date(year, 12, 25))):
        if eve.weekday() < SATURDAY and holiday in holiday_set and eve not in holiday_set:
            result.add(eve)

    return frozenset(d for d in result if d not in holiday_set)
