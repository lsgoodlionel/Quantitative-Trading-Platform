"""
交易日历测试（Wave K-b / K7）

验收基准（契约 waveKb §4.3）：三市场 2024 全年交易日数与公开数据一致。
参考值取自 `exchange_calendars`（XNYS / XHKG / XSHG）——只作为**离线核对基准**，
项目运行期不依赖该库（HANDOFF §3.6 零新增重依赖）。
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.data.models import Market
from app.engine.calendar import (
    AShareCalendar,
    HKCalendar,
    TradingCalendar,
    USCalendar,
    get_calendar,
)
from app.engine.calendar.holidays import us as us_holidays

# 公开数据核对基准：各市场各年度的交易日数
PUBLIC_SESSION_COUNTS: dict[Market, dict[int, int]] = {
    Market.US: {2023: 250, 2024: 252, 2025: 250, 2026: 251},
    Market.HK: {2023: 245, 2024: 246, 2025: 246, 2026: 247},
    Market.A: {2023: 242, 2024: 242, 2025: 243, 2026: 242},
}

# 2024 全年休市日（工作日），逐日核对用
PUBLIC_HOLIDAYS_2024: dict[Market, set[date]] = {
    Market.US: {
        date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19), date(2024, 3, 29),
        date(2024, 5, 27), date(2024, 6, 19), date(2024, 7, 4), date(2024, 9, 2),
        date(2024, 11, 28), date(2024, 12, 25),
    },
    Market.HK: {
        date(2024, 1, 1), date(2024, 2, 12), date(2024, 2, 13), date(2024, 3, 29),
        date(2024, 4, 1), date(2024, 4, 4), date(2024, 5, 1), date(2024, 5, 15),
        date(2024, 6, 10), date(2024, 7, 1), date(2024, 9, 6), date(2024, 9, 18),
        date(2024, 10, 1), date(2024, 10, 11), date(2024, 12, 25), date(2024, 12, 26),
    },
    Market.A: {
        date(2024, 1, 1), date(2024, 2, 9), date(2024, 2, 12), date(2024, 2, 13),
        date(2024, 2, 14), date(2024, 2, 15), date(2024, 2, 16), date(2024, 4, 4),
        date(2024, 4, 5), date(2024, 5, 1), date(2024, 5, 2), date(2024, 5, 3),
        date(2024, 6, 10), date(2024, 9, 16), date(2024, 9, 17), date(2024, 10, 1),
        date(2024, 10, 2), date(2024, 10, 3), date(2024, 10, 4), date(2024, 10, 7),
    },
}

ALL_MARKETS = (Market.US, Market.HK, Market.A)


@pytest.fixture(params=ALL_MARKETS, ids=lambda m: m.value)
def calendar(request) -> TradingCalendar:
    return get_calendar(request.param)


# ── 交易日数与公开数据一致 ────────────────────────────────────────

@pytest.mark.parametrize("market", ALL_MARKETS, ids=lambda m: m.value)
def test_2024_session_count_matches_public_data(market: Market) -> None:
    # Arrange
    cal = get_calendar(market)

    # Act
    count = cal.sessions_count(date(2024, 1, 1), date(2024, 12, 31))

    # Assert
    assert count == PUBLIC_SESSION_COUNTS[market][2024]


@pytest.mark.parametrize("market", ALL_MARKETS, ids=lambda m: m.value)
@pytest.mark.parametrize("year", [2023, 2024, 2025, 2026])
def test_session_counts_match_public_data_across_years(market: Market, year: int) -> None:
    cal = get_calendar(market)
    assert cal.sessions_count(date(year, 1, 1), date(year, 12, 31)) == (
        PUBLIC_SESSION_COUNTS[market][year]
    )


@pytest.mark.parametrize("market", ALL_MARKETS, ids=lambda m: m.value)
def test_2024_holidays_match_public_data_day_by_day(market: Market) -> None:
    """不只对总数——逐日比对，避免「多一个假日少一个假日」互相抵消。"""
    cal = get_calendar(market)
    expected = PUBLIC_HOLIDAYS_2024[market]

    actual = {
        d for d in _weekdays_of_year(2024) if not cal.is_session(d)
    }

    assert actual == expected


def _weekdays_of_year(year: int) -> list[date]:
    from datetime import timedelta
    days, cursor = [], date(year, 1, 1)
    while cursor.year == year:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


# ── 基础判定 ──────────────────────────────────────────────────

def test_weekend_is_never_a_session(calendar: TradingCalendar) -> None:
    assert not calendar.is_session(date(2024, 3, 2))   # 周六
    assert not calendar.is_session(date(2024, 3, 3))   # 周日


def test_ordinary_weekday_is_a_session(calendar: TradingCalendar) -> None:
    assert calendar.is_session(date(2024, 3, 5))       # 普通周二


def test_accepts_datetime_as_well_as_date(calendar: TradingCalendar) -> None:
    assert calendar.is_session(datetime(2024, 3, 5, 10, 30))


def test_next_session_skips_weekend(calendar: TradingCalendar) -> None:
    # 2024-03-01 是周五 → 下一个交易日为 03-04 周一
    assert calendar.next_session(date(2024, 3, 1)) == date(2024, 3, 4)


def test_next_session_skips_a_share_spring_festival() -> None:
    cal = get_calendar(Market.A)
    # 2024 春节休市 02-09 ~ 02-16，02-08 周四之后的交易日是 02-19 周一
    assert cal.next_session(date(2024, 2, 8)) == date(2024, 2, 19)


def test_previous_session_skips_holiday() -> None:
    cal = get_calendar(Market.US)
    assert cal.previous_session(date(2024, 7, 5)) == date(2024, 7, 3)


def test_sessions_in_range_rejects_inverted_range(calendar: TradingCalendar) -> None:
    with pytest.raises(ValueError, match="早于"):
        calendar.sessions_in_range(date(2024, 3, 5), date(2024, 3, 1))


# ── 午休 / 盘中时段判定 ────────────────────────────────────────

def test_a_share_lunch_break_is_not_trading_time() -> None:
    cal = get_calendar(Market.A)
    trading_day = date(2024, 3, 5)

    assert cal.is_trading_time(datetime.combine(trading_day, datetime.min.time().replace(hour=10)))
    assert not cal.is_trading_time(
        datetime.combine(trading_day, datetime.min.time().replace(hour=12))
    )   # 11:30–13:00 午休
    assert cal.is_trading_time(
        datetime.combine(trading_day, datetime.min.time().replace(hour=14))
    )
    assert not cal.is_trading_time(
        datetime.combine(trading_day, datetime.min.time().replace(hour=15, minute=30))
    )   # 15:00 收市


def test_hk_lunch_break_is_12_to_13() -> None:
    """港交所午休 12:00–13:00（契约写的 13:00–14:00 与实际时段不符）。"""
    cal = get_calendar(Market.HK)
    trading_day = date(2024, 3, 5)

    assert cal.is_trading_time(datetime(2024, 3, 5, 11, 30))
    assert not cal.is_trading_time(datetime(2024, 3, 5, 12, 30))
    assert cal.is_trading_time(datetime(2024, 3, 5, 13, 30))   # 午市已开
    assert cal.is_trading_time(datetime(2024, 3, 5, 15, 59))
    assert not cal.is_trading_time(datetime(2024, 3, 5, 16, 0))
    assert cal.session_windows(trading_day)


def test_us_has_no_lunch_break() -> None:
    cal = get_calendar(Market.US)
    assert cal.is_trading_time(datetime(2024, 3, 5, 12, 30))


def test_us_early_close_days() -> None:
    cal = get_calendar(Market.US)

    assert cal.is_early_close(date(2024, 11, 29))   # 感恩节次日
    assert cal.is_early_close(date(2024, 7, 3))     # 独立日前夕
    assert cal.is_early_close(date(2024, 12, 24))   # 平安夜
    assert not cal.is_early_close(date(2024, 3, 5))

    # 提前收市日 13:00 收盘
    assert cal.is_trading_time(datetime(2024, 11, 29, 12, 59))
    assert not cal.is_trading_time(datetime(2024, 11, 29, 13, 0))


def test_non_session_has_no_windows(calendar: TradingCalendar) -> None:
    assert calendar.session_windows(date(2024, 3, 2)) == ()
    assert not calendar.is_trading_time(datetime(2024, 3, 2, 10, 0))


# ── 数据缺口 / 脏数据检出 ──────────────────────────────────────

def test_missing_sessions_detects_data_gap() -> None:
    cal = get_calendar(Market.US)
    start, end = date(2024, 3, 4), date(2024, 3, 8)
    observed = [date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 8)]

    gaps = cal.missing_sessions(observed, start, end)

    assert gaps == [date(2024, 3, 6), date(2024, 3, 7)]


def test_missing_sessions_empty_when_complete() -> None:
    cal = get_calendar(Market.US)
    start, end = date(2024, 3, 4), date(2024, 3, 8)
    assert cal.missing_sessions(cal.sessions_in_range(start, end), start, end) == []


def test_non_sessions_detects_dirty_dates() -> None:
    cal = get_calendar(Market.A)
    days = [date(2024, 3, 4), date(2024, 3, 9), date(2024, 5, 1)]   # 周六 + 劳动节

    assert cal.non_sessions(days) == [date(2024, 3, 9), date(2024, 5, 1)]


# ── 年化基准 ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("market", "expected"), [(Market.US, 252), (Market.HK, 246), (Market.A, 242)]
)
def test_sessions_per_year_matches_measured_2024(market: Market, expected: int) -> None:
    cal = get_calendar(market)
    measured = cal.sessions_per_year(date(2024, 1, 1), date(2024, 12, 31))
    # 2024 是闰年（366 天），折算到 365.25 天会略微下调
    assert measured == pytest.approx(expected * 365.25 / 366, rel=1e-9)


def test_sessions_per_year_falls_back_on_short_span(calendar: TradingCalendar) -> None:
    """区间太短时不采信实测值，回退市场默认常数。"""
    measured = calendar.sessions_per_year(date(2024, 3, 1), date(2024, 3, 20))
    assert measured == float(calendar.default_sessions_per_year)


def test_default_sessions_per_year_matches_legacy_constants() -> None:
    """日历默认常数必须与 metrics 里的既有常数一致，否则接入即改变基线。"""
    from app.engine.backtest.metrics import (
        TRADING_DAYS_A,
        TRADING_DAYS_HK,
        TRADING_DAYS_US,
    )

    assert get_calendar(Market.US).default_sessions_per_year == TRADING_DAYS_US
    assert get_calendar(Market.HK).default_sessions_per_year == TRADING_DAYS_HK
    assert get_calendar(Market.A).default_sessions_per_year == TRADING_DAYS_A


# ── 静态表覆盖范围外的降级行为 ─────────────────────────────────

@pytest.mark.parametrize("market", [Market.HK, Market.A], ids=["HK", "A"])
def test_uncovered_year_degrades_to_weekday_only_with_warning(
    market: Market, caplog
) -> None:
    cal = get_calendar(market)
    uncovered_year = max(cal.coverage_years) + 5
    # 取该年 1 月的第一个工作日，避免恰好撞上周末
    weekday = next(d for d in _weekdays_of_year(uncovered_year))

    with caplog.at_level("WARNING"):
        # 表外年份的工作日一律判为交易日（只排除周末），并且必须留下告警
        assert cal.is_session(weekday) is True

    assert any("假日表未覆盖" in r.message for r in caplog.records)


def test_us_calendar_covers_arbitrary_years() -> None:
    """美股假日由规则推导，静态表覆盖区间不构成限制。"""
    cal = get_calendar(Market.US)
    assert not cal.is_session(date(2010, 12, 24))   # 12/25 周六 → 12/24 补假
    assert not cal.is_session(date(2015, 4, 3))     # 耶稣受难节
    assert cal.is_session(date(2015, 6, 19))        # 2022 前无六月节


def test_new_year_on_saturday_is_not_observed() -> None:
    """NYSE 元旦落在周六不补假 —— 2022-01-01 是周六，2021-12-31 照常开市。"""
    cal = get_calendar(Market.US)
    assert cal.is_session(date(2021, 12, 31))


def test_special_closure_is_honored() -> None:
    cal = get_calendar(Market.US)
    assert not cal.is_session(date(2025, 1, 9))     # 卡特国葬
    assert not cal.is_session(date(2012, 10, 29))   # 飓风桑迪


def test_easter_algorithm_known_values() -> None:
    assert us_holidays.easter_sunday(2024) == date(2024, 3, 31)
    assert us_holidays.easter_sunday(2025) == date(2025, 4, 20)
    assert us_holidays.easter_sunday(2026) == date(2026, 4, 5)


# ── 注册表 ────────────────────────────────────────────────────

def test_get_calendar_returns_singleton_per_market() -> None:
    assert get_calendar(Market.US) is get_calendar(Market.US)


def test_get_calendar_types() -> None:
    assert isinstance(get_calendar(Market.US), USCalendar)
    assert isinstance(get_calendar(Market.HK), HKCalendar)
    assert isinstance(get_calendar(Market.A), AShareCalendar)


def test_get_calendar_rejects_unknown_market() -> None:
    with pytest.raises(ValueError, match="暂不支持"):
        get_calendar("CRYPTO")   # type: ignore[arg-type]


# ── 引擎接入（默认关闭 = 零影响） ──────────────────────────────

def _daily_bars(n: int, start: date = date(2024, 1, 2)):
    """含周末的连续自然日 bar —— 刻意做成「脏数据」，用来验证日历校验能力。"""
    from datetime import UTC, timedelta

    from app.data.models import Bar, Frequency

    return [
        Bar(
            time=datetime(2024, 1, 2, tzinfo=UTC) + timedelta(days=i),
            symbol="AAPL",
            market=Market.US,
            frequency=Frequency.DAY_1,
            open=100.0, high=101.0, low=99.0, close=100.0, volume=1_000_000,
        )
        for i in range(n)
    ]


class _NoopStrategy:
    """最小策略桩：只需要引擎能跑完整个循环。"""

    name = "noop"
    _params: dict = {}

    def on_start(self, ctx) -> None: ...
    def on_bar(self, ctx) -> None: ...
    def on_stop(self, ctx) -> None: ...


def test_calendar_defaults_to_none_and_adds_no_report_keys() -> None:
    """红线：不配置日历时，年化基准与报告结构都必须与接入前一致。"""
    from app.engine.backtest.engine import BacktestConfig, BacktestEngine

    cfg = BacktestConfig(market=Market.US)
    assert cfg.calendar is None

    result = BacktestEngine(cfg).run(_NoopStrategy(), _daily_bars(300))

    assert "calendar" not in result.report
    assert "data_gaps" not in result.report


def test_calendar_reports_non_sessions_and_gaps() -> None:
    from app.engine.backtest.engine import BacktestConfig, BacktestEngine

    cfg = BacktestConfig(market=Market.US, calendar=get_calendar(Market.US))
    result = BacktestEngine(cfg).run(_NoopStrategy(), _daily_bars(300))

    diagnostics = result.report["calendar"]

    assert diagnostics["name"] == "NYSE"
    assert diagnostics["observed_bars"] == 300
    # 自然日序列包含周末与假日 → 必然检出非交易日的 bar
    assert len(diagnostics["non_session_bars"]) > 80
    # 反过来，日历里的交易日全都有数据 → 无缺口
    assert result.report["data_gaps"] == []


def test_calendar_detects_missing_sessions_in_engine() -> None:
    from app.engine.backtest.engine import BacktestConfig, BacktestEngine

    bars = _daily_bars(300)
    # 抠掉一段真实交易日
    kept = [b for b in bars if b.time.date() not in {date(2024, 3, 5), date(2024, 3, 6)}]

    cfg = BacktestConfig(market=Market.US, calendar=get_calendar(Market.US))
    result = BacktestEngine(cfg).run(_NoopStrategy(), kept)

    assert "2024-03-05" in result.report["data_gaps"]
    assert "2024-03-06" in result.report["data_gaps"]


def test_calendar_switches_annualization_to_measured_value() -> None:
    """契约 §2.3 第 3 点：配置日历后年化基准改用实测值。"""
    from app.engine.backtest.engine import BacktestConfig, _resolve_trading_days_per_year

    bars = _daily_bars(366)

    without = _resolve_trading_days_per_year(BacktestConfig(market=Market.US), bars)
    with_cal = _resolve_trading_days_per_year(
        BacktestConfig(market=Market.US, calendar=get_calendar(Market.US)), bars
    )

    assert without == 252                 # 常数，不受日历影响
    assert 245 <= with_cal <= 255         # 实测值（2024 闰年折算后略低于 252）

