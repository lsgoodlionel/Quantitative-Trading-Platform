"""Walk-Forward 分析单元测试（C2）

覆盖：
- _slice_windows 滚动/锚定窗口切分正确（train/test 不重叠、窗口数符合预期、步进 = test_size）
- run_walk_forward 端到端窗口数、IS/OOS 指标传递、汇总统计（效率/一致性）
- 输入校验（模式非法、窗口过小、数据不足）
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.walkforward import (
    WalkForwardOutcome,
    _slice_windows,
    run_walk_forward,
)


# ── 测试辅助 ─────────────────────────────────────────────────────

def _make_bars(n: int, base_price: float = 100.0) -> list[Bar]:
    """生成 n 根等间隔日线 bar（价格单调，避免随机性）。"""
    start = datetime(2024, 1, 2, tzinfo=timezone.utc)
    bars: list[Bar] = []
    for i in range(n):
        price = base_price + i
        bars.append(
            Bar(
                time=start + timedelta(days=i),
                symbol="AAPL",
                market=Market.US,
                frequency=Frequency.DAY_1,
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price,
                volume=100_000,
            )
        )
    return bars


class TestSliceWindows:
    """_slice_windows 半开区间索引切分。"""

    def test_rolling_windows_are_non_overlapping_train_test(self):
        # Arrange: n=100, train=40, test=20
        # Act
        windows = _slice_windows(100, train_size=40, test_size=20, mode="rolling")

        # Assert: 每个窗口 train_hi == test_lo（相邻不重叠），窗口内 test 紧跟 train
        for tr_lo, tr_hi, te_lo, te_hi in windows:
            assert tr_hi == te_lo
            assert te_hi - te_lo == 20
            assert tr_hi - tr_lo == 40  # rolling 固定训练长度

    def test_rolling_window_count_and_step(self):
        # Arrange: test_lo 从 40 起步，步进 20，直到 test_lo+20 <= 100
        # test_lo: 40,60,80 → 3 个窗口（100 时 100+20>100 停止）
        # Act
        windows = _slice_windows(100, train_size=40, test_size=20, mode="rolling")

        # Assert
        assert len(windows) == 3
        test_starts = [te_lo for _, _, te_lo, _ in windows]
        assert test_starts == [40, 60, 80]

    def test_rolling_test_segments_do_not_overlap(self):
        # Arrange
        windows = _slice_windows(120, train_size=30, test_size=15, mode="rolling")

        # Act: 收集所有测试区间
        test_ranges = [(te_lo, te_hi) for _, _, te_lo, te_hi in windows]

        # Assert: 相邻测试段首尾相接、互不重叠
        for (_, prev_hi), (nxt_lo, _) in zip(test_ranges, test_ranges[1:]):
            assert prev_hi == nxt_lo

    def test_anchored_train_start_is_pinned_to_zero(self):
        # Arrange / Act
        windows = _slice_windows(100, train_size=40, test_size=20, mode="anchored")

        # Assert: 训练起点恒为 0，训练窗口随时间扩张
        train_starts = [tr_lo for tr_lo, _, _, _ in windows]
        train_lengths = [tr_hi - tr_lo for tr_lo, tr_hi, _, _ in windows]
        assert all(s == 0 for s in train_starts)
        assert train_lengths == sorted(train_lengths)  # 单调不减（扩张）
        assert train_lengths[0] == 40 and train_lengths[-1] == 80

    def test_insufficient_data_yields_no_windows(self):
        # Arrange: 数据刚好不足一个 train+test
        # Act
        windows = _slice_windows(50, train_size=40, test_size=20, mode="rolling")

        # Assert
        assert windows == []


class TestRunWalkForward:
    """run_walk_forward 端到端。"""

    @staticmethod
    def _optimize(bars: list[Bar]) -> dict:
        # 训练窗口首根 bar 长度作为“最优参数”，验证 params 正确透传
        return {"period": len(bars)}

    @staticmethod
    def _backtest(params: dict, bars: list[Bar]) -> dict:
        # IS(训练)固定给正收益，OOS(测试)按窗口 bar 数派生，便于断言
        n = len(bars)
        return {
            "sharpe_ratio": 1.5 if n >= 40 else 0.8,
            "total_return_pct": 5.0 if n >= 40 else 2.0,
        }

    def test_window_count_matches_slicing(self):
        # Arrange
        bars = _make_bars(100)

        # Act
        outcome = run_walk_forward(
            bars, self._optimize, self._backtest,
            train_size=40, test_size=20, mode="rolling",
        )

        # Assert
        assert isinstance(outcome, WalkForwardOutcome)
        assert outcome.total_windows == 3
        assert len(outcome.windows) == 3

    def test_train_test_bars_do_not_overlap_by_time(self):
        # Arrange
        bars = _make_bars(100)

        # Act
        outcome = run_walk_forward(
            bars, self._optimize, self._backtest,
            train_size=40, test_size=20,
        )

        # Assert: 每个窗口训练结束时间 < 测试起始时间（样本外在训练之后）
        for w in outcome.windows:
            assert w.train_end < w.test_start
            assert w.train_bars == 40
            assert w.test_bars == 20

    def test_best_params_passed_from_optimize(self):
        # Arrange
        bars = _make_bars(100)

        # Act
        outcome = run_walk_forward(
            bars, self._optimize, self._backtest,
            train_size=40, test_size=20,
        )

        # Assert: optimize 返回训练 bar 数，rolling 下恒为 40
        assert all(w.best_params == {"period": 40} for w in outcome.windows)

    def test_aggregate_efficiency_and_consistency(self):
        # Arrange
        bars = _make_bars(100)

        # Act
        outcome = run_walk_forward(
            bars, self._optimize, self._backtest,
            train_size=40, test_size=20,
        )

        # Assert: IS sharpe=1.5, OOS sharpe=0.8 → 效率 0.8/1.5≈0.5333
        assert outcome.avg_is_sharpe == pytest.approx(1.5)
        assert outcome.avg_oos_sharpe == pytest.approx(0.8)
        assert outcome.oos_is_efficiency == pytest.approx(0.5333, abs=1e-3)
        # OOS 收益恒为正 → 一致性 100%
        assert outcome.oos_consistency == pytest.approx(1.0)
        assert outcome.oos_win_windows == 3

    def test_anchored_mode_expands_training_window(self):
        # Arrange
        bars = _make_bars(100)

        # Act
        outcome = run_walk_forward(
            bars, self._optimize, self._backtest,
            train_size=40, test_size=20, mode="anchored",
        )

        # Assert: 训练 bar 数随窗口扩张
        train_sizes = [w.train_bars for w in outcome.windows]
        assert train_sizes == sorted(train_sizes)
        assert train_sizes[0] == 40
        assert train_sizes[-1] > 40


class TestValidation:
    """run_walk_forward 输入校验。"""

    def test_unknown_mode_raises(self):
        # Arrange
        bars = _make_bars(100)

        # Act / Assert
        with pytest.raises(ValueError, match="未知模式"):
            run_walk_forward(
                bars, TestRunWalkForward._optimize, TestRunWalkForward._backtest,
                train_size=40, test_size=20, mode="expanding",
            )

    def test_too_small_window_raises(self):
        # Arrange
        bars = _make_bars(100)

        # Act / Assert: test_size < 5
        with pytest.raises(ValueError, match="至少需"):
            run_walk_forward(
                bars, TestRunWalkForward._optimize, TestRunWalkForward._backtest,
                train_size=40, test_size=3,
            )

    def test_insufficient_data_raises(self):
        # Arrange: 数据 < train+test
        bars = _make_bars(30)

        # Act / Assert
        with pytest.raises(ValueError, match="数据不足"):
            run_walk_forward(
                bars, TestRunWalkForward._optimize, TestRunWalkForward._backtest,
                train_size=40, test_size=20,
            )

    def test_all_windows_fail_raises(self):
        # Arrange
        bars = _make_bars(100)

        def _bad_optimize(_bars: list[Bar]) -> dict:
            raise RuntimeError("optimize boom")

        # Act / Assert
        with pytest.raises(ValueError, match="均评估失败"):
            run_walk_forward(
                bars, _bad_optimize, TestRunWalkForward._backtest,
                train_size=40, test_size=20,
            )
