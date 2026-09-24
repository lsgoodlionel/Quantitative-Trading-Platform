"""
归档缺口检测与增量补齐计划（M1）

`find_gaps` 回答的问题是：「我想要 [want_start, want_end]，手上已有 have 这些日子，
还差哪几段？」结果用于向在线源发起最少次数的增量请求。

**与 K7 交易日历的关系**：给了日历就按交易日算缺口（周末/假日不算缺），
没给就按自然日的连续段。**默认不给** —— 引入日历会让缺口判定依赖假日表的准确性，
而假日表本身是尽力而为的；宁可多请求一次，也不要因为假日表漏了一天就永远补不上。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只用于类型标注，避免 data → engine 的运行期依赖
    from app.engine.calendar.base import TradingCalendar

_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class Gap:
    """一段缺失区间（闭区间）。"""

    start: Date
    end: Date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"Gap end {self.end} 早于 start {self.start}")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def find_gaps(
    have: list[Date],
    want_start: Date,
    want_end: Date,
    calendar: TradingCalendar | None = None,
) -> list[Gap]:
    """
    计算 [want_start, want_end] 内尚未覆盖的区间。

    Args:
        have:       已有数据的日期（可乱序、可含区间外的日期、可重复）
        want_start: 期望区间起（含）
        want_end:   期望区间止（含）
        calendar:   给了就只按交易日判缺；None 则按自然日

    Returns:
        按时间升序的缺口列表；完全命中返回空列表。
    """
    if want_end < want_start:
        raise ValueError(f"want_end {want_end} 早于 want_start {want_start}")

    expected = _expected_days(want_start, want_end, calendar)
    if not expected:
        return []

    owned = {d for d in have if want_start <= d <= want_end}
    missing = [d for d in expected if d not in owned]
    return _group_consecutive(missing, expected)


def boundary_gaps(
    coverage: tuple[Date, Date] | None, want_start: Date, want_end: Date
) -> list[Gap]:
    """
    只按**覆盖区间的两端**算缺口（头段 + 尾段），忽略内部空洞。

    `DataService` 的归档命中判定用这个而不是 `find_gaps`：不带日历时，
    周末与假日在 `find_gaps` 眼里全是缺口，于是每次取数都会退化成全量在线请求，
    归档等于白做。而两端缺口是无歧义的 —— 归档里根本没有那些日子的数据。
    内部空洞（停牌、数据源当日缺失）交给显式的 archive download 任务去补。
    """
    if want_end < want_start:
        raise ValueError(f"want_end {want_end} 早于 want_start {want_start}")
    if coverage is None:
        return [Gap(want_start, want_end)]

    have_start, have_end = coverage
    gaps: list[Gap] = []
    if want_start < have_start:
        gaps.append(Gap(want_start, min(have_start - _ONE_DAY, want_end)))
    if want_end > have_end:
        gaps.append(Gap(max(have_end + _ONE_DAY, want_start), want_end))
    return gaps


# ── 内部 ──────────────────────────────────────────────────────
def _expected_days(
    start: Date, end: Date, calendar: TradingCalendar | None
) -> list[Date]:
    if calendar is not None:
        return list(calendar.sessions_in_range(start, end))
    days: list[Date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += _ONE_DAY
    return days


def _group_consecutive(missing: list[Date], expected: list[Date]) -> list[Gap]:
    """把缺失日按「在 expected 序列里相邻」合并成段。"""
    if not missing:
        return []

    position = {day: i for i, day in enumerate(expected)}
    gaps: list[Gap] = []
    segment_start = segment_end = missing[0]

    for day in missing[1:]:
        if position[day] == position[segment_end] + 1:
            segment_end = day
            continue
        gaps.append(Gap(segment_start, segment_end))
        segment_start = segment_end = day

    gaps.append(Gap(segment_start, segment_end))
    return gaps
