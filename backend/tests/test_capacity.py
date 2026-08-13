"""容量 · 换手 · 杠杆测试（Wave N-a / N2）

验收要点（契约 waveNa §五.2）：
- 换手率 = 当日成交额 / 当日净值，与手算小样本对拍
- 杠杆用**绝对值**加总：多空各半的组合杠杆 ≈ 1.0 而不是 0
- 容量估计返回体含 assumptions
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.capacity import (
    CapacityEstimate,
    build_capacity_section,
    estimate_capacity,
    leverage_series,
    turnover_series,
)
from app.engine.backtest.daily_result import (
    ContractDailyResult,
    PortfolioDailyResult,
)
from app.engine.backtest.report_sections import build_extended_sections


def _contract(
    symbol: str,
    day: date,
    *,
    close: float,
    end_pos: int,
    turnover: float = 0.0,
) -> ContractDailyResult:
    """构造一条最小可用的单标的日结（只填本模块关心的字段）。"""
    return ContractDailyResult(
        date=day,
        symbol=symbol,
        close_price=close,
        pre_close=close,
        trades=(),
        trade_count=0,
        start_pos=end_pos,
        end_pos=end_pos,
        turnover=turnover,
        commission=0.0,
        slippage=0.0,
        trading_pnl=0.0,
        holding_pnl=0.0,
        total_pnl=0.0,
        net_pnl=0.0,
    )


def _daily(day: date, contracts: list[ContractDailyResult]) -> PortfolioDailyResult:
    return PortfolioDailyResult(
        date=day,
        trade_count=0,
        turnover=sum(c.turnover for c in contracts),
        commission=0.0,
        slippage=0.0,
        trading_pnl=0.0,
        holding_pnl=0.0,
        total_pnl=0.0,
        net_pnl=0.0,
        contracts={c.symbol: c for c in contracts},
    )


def _equity(days: list[date], values: list[float]) -> pd.Series:
    index = pd.DatetimeIndex([datetime(d.year, d.month, d.day) for d in days])
    return pd.Series(values, index=index)


def _bar(symbol: str, day: date, *, close: float, volume: int) -> Bar:
    return Bar(
        time=datetime(day.year, day.month, day.day, tzinfo=UTC),
        symbol=symbol,
        market=Market.US,
        frequency=Frequency.DAY_1,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
    )


class TestTurnoverSeries:
    def test_matches_hand_computed_ratio(self) -> None:
        # Arrange: 两天成交额 5000 / 2000，净值 100000 / 50000
        days = [date(2024, 1, 2), date(2024, 1, 3)]
        daily = [
            _daily(days[0], [_contract("AAPL", days[0], close=10.0, end_pos=100, turnover=5000.0)]),
            _daily(days[1], [_contract("AAPL", days[1], close=10.0, end_pos=100, turnover=2000.0)]),
        ]
        equity = _equity(days, [100_000.0, 50_000.0])

        # Act
        series = turnover_series(daily, equity)

        # Assert
        assert list(series.values) == pytest.approx([0.05, 0.04])

    def test_skips_days_without_equity(self) -> None:
        days = [date(2024, 1, 2), date(2024, 1, 3)]
        daily = [
            _daily(d, [_contract("AAPL", d, close=10.0, end_pos=10, turnover=100.0)]) for d in days
        ]
        equity = _equity(days[:1], [1000.0])

        series = turnover_series(daily, equity)

        assert len(series) == 1

    def test_empty_input_returns_empty_series(self) -> None:
        assert turnover_series([], pd.Series(dtype=float)).empty


class TestLeverageSeries:
    def test_long_short_hedged_book_is_not_zero_leverage(self) -> None:
        # Arrange: 多 100 股 @10 与空 100 股 @10 —— 带符号加总会得 0
        day = date(2024, 1, 2)
        daily = [
            _daily(
                day,
                [
                    _contract("AAPL", day, close=10.0, end_pos=100),
                    _contract("MSFT", day, close=10.0, end_pos=-100),
                ],
            )
        ]
        equity = _equity([day], [2000.0])

        # Act
        series = leverage_series(daily, equity)

        # Assert: |1000| + |-1000| = 2000，除以净值 2000 → 1.0
        assert float(series.iloc[0]) == pytest.approx(1.0)

    def test_pure_long_book_leverage(self) -> None:
        day = date(2024, 1, 2)
        daily = [_daily(day, [_contract("AAPL", day, close=50.0, end_pos=100)])]
        equity = _equity([day], [10_000.0])

        series = leverage_series(daily, equity)

        assert float(series.iloc[0]) == pytest.approx(0.5)

    def test_flat_book_is_zero(self) -> None:
        day = date(2024, 1, 2)
        daily = [_daily(day, [_contract("AAPL", day, close=50.0, end_pos=0)])]
        equity = _equity([day], [10_000.0])

        assert float(leverage_series(daily, equity).iloc[0]) == pytest.approx(0.0)


class TestEstimateCapacity:
    def _sample(self) -> tuple[list[PortfolioDailyResult], dict[str, list[Bar]]]:
        days = [date(2024, 1, 2), date(2024, 1, 3)]
        daily = [
            _daily(d, [_contract("AAPL", d, close=10.0, end_pos=100, turnover=1000.0)])
            for d in days
        ]
        bars = {"AAPL": [_bar("AAPL", d, close=10.0, volume=100_000) for d in days]}
        return daily, bars

    def test_returns_assumptions(self) -> None:
        daily, bars = self._sample()

        est = estimate_capacity(daily, bars, reference_equity=100_000.0)

        assert isinstance(est, CapacityEstimate)
        assert est.assumptions
        assert est.to_dict()["is_rough_estimate"] is True

    def test_scale_factor_binds_on_adv_share(self) -> None:
        # Arrange: ADV = 10 * 100000 = 1_000_000；5% = 50_000；实际成交 1000
        daily, bars = self._sample()

        # Act
        est = estimate_capacity(daily, bars, reference_equity=100_000.0, max_adv_share=0.05)

        # Assert: 可放大 50 倍 → 容量 = 50 × 100_000
        assert est.scale_factor == pytest.approx(50.0)
        assert est.capacity == pytest.approx(5_000_000.0)
        assert est.binding_symbol == "AAPL"

    def test_no_tradable_day_yields_unknown_capacity(self) -> None:
        day = date(2024, 1, 2)
        daily = [_daily(day, [_contract("AAPL", day, close=10.0, end_pos=100)])]

        est = estimate_capacity(daily, {}, reference_equity=100_000.0)

        assert est.capacity is None
        assert est.sample_days == 0
        assert est.assumptions   # 即便算不出也要交代前提


class TestCapacitySection:
    def test_section_has_three_blocks(self) -> None:
        days = [date(2024, 1, 2), date(2024, 1, 3)]
        daily = [
            _daily(d, [_contract("AAPL", d, close=10.0, end_pos=100, turnover=1000.0)])
            for d in days
        ]
        bars = {"AAPL": [_bar("AAPL", d, close=10.0, volume=100_000) for d in days]}
        equity = _equity(days, [100_000.0, 101_000.0])

        section = build_capacity_section(daily, equity, bars)

        assert set(section) == {"turnover", "leverage", "capacity"}
        assert section["turnover"]["avg_daily_pct"] == pytest.approx(0.995, abs=1e-2)
        assert section["capacity"]["is_rough_estimate"] is True

    def test_returns_none_without_daily_results(self) -> None:
        assert build_capacity_section([], pd.Series(dtype=float), {}) is None


class TestExtendedSectionsWiring:
    """N-a 三个 section 必须是**可空**的，不传就不出现数据（而非一堆 0）。"""

    def _inputs(self):
        days = [date(2020, 2, 20), date(2020, 3, 20)]
        daily = [
            _daily(d, [_contract("AAPL", d, close=10.0, end_pos=100, turnover=1000.0)])
            for d in days
        ]
        bars = {"AAPL": [_bar("AAPL", d, close=10.0, volume=100_000) for d in days]}
        equity = _equity(days, [100_000.0, 80_000.0])
        return daily, bars, equity

    def test_absent_when_optional_inputs_missing(self) -> None:
        _, _, equity = self._inputs()

        sections = build_extended_sections(
            equity_curve=equity, fills=[], starting_balance=100_000.0
        )

        assert sections["capacity_analysis"] is None
        assert sections["crisis_windows"] is None
        assert sections["rejected_signals"] is None
        # 既有 section 的键一个不少
        assert "tag_metrics" in sections
        assert "drawdown_periods" in sections

    def test_present_when_inputs_supplied(self) -> None:
        daily, bars, equity = self._inputs()

        sections = build_extended_sections(
            equity_curve=equity,
            fills=[],
            starting_balance=100_000.0,
            daily_results=daily,
            bars_by_symbol=bars,
            market="US",
        )

        assert sections["capacity_analysis"]["capacity"]["assumptions"]
        # 2020 疫情崩盘落在回测期内
        assert [w["name"] for w in sections["crisis_windows"]["windows"]] == [
            "2020 疫情崩盘"
        ]
