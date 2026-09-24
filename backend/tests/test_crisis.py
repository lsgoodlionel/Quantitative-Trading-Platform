"""危机区间分段对照测试（Wave N-a / N3）

验收要点（契约 waveNa §五.3）：
- 回测期覆盖某危机 → 该行有数据
- 回测期与危机无交集 → **跳过**，不出现一行全 0
- 市场不匹配（A股策略遇到 2008）→ 跳过
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.data.models import Market
from app.engine.backtest.crisis import (
    CRISIS_WINDOWS,
    SKIP_NO_OVERLAP,
    SKIP_NOT_APPLICABLE,
    build_crisis_section,
    crisis_performance,
    skipped_crisis_windows,
)


def _equity(start: str, end: str, *, start_value: float = 100.0, end_value: float = 80.0):
    """构造一条从 start_value 线性走到 end_value 的日频净值曲线。"""
    index = pd.date_range(start=start, end=end, freq="D")
    values = np.linspace(start_value, end_value, num=len(index))
    return pd.Series(values, index=index)


class TestCrisisWindows:
    def test_windows_are_chronological_and_well_formed(self) -> None:
        for window in CRISIS_WINDOWS:
            assert window.start < window.end
            assert window.markets
            assert window.name

    def test_windows_are_immutable(self) -> None:
        with pytest.raises((AttributeError, TypeError)):
            CRISIS_WINDOWS[0].name = "改不了"  # type: ignore[misc]


class TestCrisisPerformance:
    def test_covered_window_produces_a_row(self) -> None:
        # Arrange: 2020 疫情崩盘完整落在回测期内
        equity = _equity("2020-01-01", "2020-06-30", start_value=100.0, end_value=70.0)

        # Act
        rows = crisis_performance(equity, Market.US)

        # Assert
        names = [r["name"] for r in rows]
        assert "2020 疫情崩盘" in names
        row = next(r for r in rows if r["name"] == "2020 疫情崩盘")
        assert row["return_pct"] < 0
        assert row["is_partial"] is False
        assert row["trading_days"] > 1

    def test_window_without_overlap_is_skipped_not_zeroed(self) -> None:
        # Arrange: 2021 全年，与任何硬编码危机区间都无交集
        equity = _equity("2021-01-01", "2021-12-31")

        # Act
        rows = crisis_performance(equity, Market.US)

        # Assert: 不是「一行全 0」，而是根本不出现
        assert rows == []

    def test_market_mismatch_is_skipped(self) -> None:
        # Arrange: A股策略跑在 2008 金融危机期间 —— 该窗口只标注 US/HK
        equity = _equity("2007-10-01", "2009-04-01")

        rows = crisis_performance(equity, Market.A)

        assert all(r["name"] != "2008 金融危机" for r in rows)

    def test_partial_overlap_is_flagged(self) -> None:
        # Arrange: 只覆盖 2020 崩盘的后半段
        equity = _equity("2020-03-01", "2020-12-31")

        rows = crisis_performance(equity, Market.US)

        row = next(r for r in rows if r["name"] == "2020 疫情崩盘")
        assert row["is_partial"] is True
        assert row["covered_start"] >= "2020-03-01"

    def test_single_point_overlap_is_skipped(self) -> None:
        # 只有 1 个交易日落在窗口内，算不出收益率 → 跳过
        equity = pd.Series([100.0], index=pd.DatetimeIndex(["2020-03-23"]))

        assert crisis_performance(equity, Market.US) == []

    def test_empty_equity_returns_empty(self) -> None:
        assert crisis_performance(pd.Series(dtype=float), Market.US) == []

    def test_accepts_plain_market_string(self) -> None:
        equity = _equity("2020-01-01", "2020-06-30")

        assert crisis_performance(equity, "US") == crisis_performance(equity, Market.US)

    def test_tz_aware_curve_keeps_local_calendar_dates(self) -> None:
        # Arrange: A 股口径的 tz-aware 曲线，恰好从 2015 股灾**首日**开始。
        # 若去时区时折算到 UTC，首个时点会被推到前一天，窗口边界随之错开。
        naive = _equity("2015-06-12", "2015-08-31")
        aware = naive.tz_localize("Asia/Shanghai")

        # Act
        rows = crisis_performance(aware, Market.A)

        # Assert
        row = next(r for r in rows if r["name"] == "2015 A股股灾")
        assert row["covered_start"] == "2015-06-12"

    def test_tz_aware_and_naive_agree(self) -> None:
        naive = _equity("2020-01-01", "2020-06-30")
        aware = naive.tz_localize("America/New_York")

        assert crisis_performance(aware, Market.US) == crisis_performance(
            naive, Market.US
        )


class TestSkippedWindows:
    def test_skip_reasons_are_explicit(self) -> None:
        equity = _equity("2021-01-01", "2021-12-31")

        skipped = skipped_crisis_windows(equity, Market.A)

        reasons = {s["reason"] for s in skipped}
        assert SKIP_NO_OVERLAP in reasons
        assert SKIP_NOT_APPLICABLE in reasons

    def test_covered_window_not_in_skipped(self) -> None:
        equity = _equity("2020-01-01", "2020-06-30")

        skipped = skipped_crisis_windows(equity, Market.US)

        assert all(s["name"] != "2020 疫情崩盘" for s in skipped)


class TestCrisisSection:
    def test_section_reports_windows_and_skips(self) -> None:
        equity = _equity("2020-01-01", "2020-06-30")

        section = build_crisis_section(equity, Market.US)

        assert section is not None
        assert section["market"] == "US"
        assert len(section["windows"]) == 1
        assert section["skipped"]

    def test_returns_none_without_equity(self) -> None:
        assert build_crisis_section(pd.Series(dtype=float), Market.US) is None
