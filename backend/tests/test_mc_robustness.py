"""蒙特卡洛稳健性 —— 逐笔重采样单元测试（C4）

覆盖：
- bootstrap 逐笔有放回重采样：置信区间结构正确（ci95 宽于 ci90、分位单调）
- shuffle 无放回打乱：保持交易集合 → 总收益不变（std≈0）
- _METRIC_DIRECTION 方向正确，尤其 max_drawdown_pct=True：
    * 原始回撤优于（浅于）95% 重采样 → p 值小、5% 显著
    * 原始回撤最差（深）→ p 值≈1、不显著（反向验证方向）
- prob_profit / prob_beat_original 语义
- 空交易 / 单交易 / 不足 5 笔边界抛错，n_scenarios<1 抛错，未知方法抛错
"""

from __future__ import annotations

import numpy as np
import pytest

from app.engine.backtest.mc_robustness import (
    ALPHA_5_PERCENT,
    MIN_TRADES,
    McRobustnessResult,
    _METRIC_DIRECTION,
    run_mc_robustness,
)

INITIAL_CASH = 1000.0


# ── 测试辅助 ─────────────────────────────────────────────────────

def _interleaved_losses(k_loss: int, m_gain: int, loss: float = -1.0, gain: float = 2.0) -> list[float]:
    """把亏损均匀铺开 → 相邻亏损游程最短 → 原始回撤最浅（最优）。"""
    n = k_loss + m_gain
    seq = [gain] * n
    step = n // k_loss
    for j in range(k_loss):
        seq[min(j * step + step // 2, n - 1)] = loss
    return seq


def _get_metric(result: McRobustnessResult, name: str):
    return next(m for m in result.metrics if m.name == name)


class TestBootstrapConfidenceInterval:
    """bootstrap 重采样置信区间结构。"""

    def test_returns_all_four_metrics(self):
        # Arrange
        pnls = _interleaved_losses(10, 20)

        # Act
        result = run_mc_robustness(pnls, INITIAL_CASH, n_scenarios=1000, method="bootstrap", seed=42)

        # Assert
        assert isinstance(result, McRobustnessResult)
        assert result.method == "bootstrap"
        assert result.n_trades == 30
        assert result.n_scenarios == 1000
        assert {m.name for m in result.metrics} == set(_METRIC_DIRECTION)

    def test_ci95_wider_than_ci90_and_quantiles_monotonic(self):
        # Arrange
        pnls = _interleaved_losses(10, 20)

        # Act
        result = run_mc_robustness(pnls, INITIAL_CASH, n_scenarios=2000, method="bootstrap", seed=42)
        ret = _get_metric(result, "total_return_pct")

        # Assert: 95% 区间包含 90% 区间；分位数单调不减
        assert ret.ci95_lower <= ret.ci90_lower <= ret.p50 <= ret.ci90_upper <= ret.ci95_upper
        assert ret.p5 <= ret.p25 <= ret.p50 <= ret.p75 <= ret.p95
        assert ret.min <= ret.p5
        assert ret.p95 <= ret.max

    def test_ci_bounds_match_percentiles(self):
        # Arrange: ci90 = [p5,p95]，与 metric 中 p5/p95 一致
        pnls = _interleaved_losses(10, 20)

        # Act
        result = run_mc_robustness(pnls, INITIAL_CASH, n_scenarios=1500, method="bootstrap", seed=3)
        ret = _get_metric(result, "total_return_pct")

        # Assert
        assert ret.ci90_lower == pytest.approx(ret.p5)
        assert ret.ci90_upper == pytest.approx(ret.p95)

    def test_bootstrap_total_return_varies(self):
        # Arrange: 有放回重采样改变交易集合 → 总收益有波动
        pnls = _interleaved_losses(10, 20)

        # Act
        result = run_mc_robustness(pnls, INITIAL_CASH, n_scenarios=2000, method="bootstrap", seed=42)
        ret = _get_metric(result, "total_return_pct")

        # Assert
        assert ret.std > 0.0

    def test_envelope_and_curve_lengths(self):
        # Arrange
        pnls = _interleaved_losses(8, 22)

        # Act
        result = run_mc_robustness(pnls, INITIAL_CASH, n_scenarios=500, method="bootstrap", seed=1)

        # Assert: 交易数 < 150 → 净值曲线含起始点 = n+1，不降采样
        assert len(result.original_curve) == result.n_trades + 1
        assert len(result.envelope) == result.n_trades + 1
        assert result.envelope[0]["step"] == 0
        assert set(result.envelope[0]) == {"step", "p5", "p25", "p50", "p75", "p95"}


class TestShuffleInvariant:
    """shuffle 打乱保持交易集合。"""

    def test_shuffle_preserves_total_return(self):
        # Arrange: 打乱顺序不改变盈亏集合 → 总收益恒定
        pnls = [2.0, -1.0, 3.0, -2.0, 1.5, -0.5, 2.0, -1.0]

        # Act
        result = run_mc_robustness(pnls, INITIAL_CASH, n_scenarios=500, method="shuffle", seed=7)
        ret = _get_metric(result, "total_return_pct")

        # Assert
        assert result.method == "shuffle"
        assert ret.std == pytest.approx(0.0)
        assert ret.mean == pytest.approx(ret.original)

    def test_shuffle_drawdown_still_varies(self):
        # Arrange: 顺序改变回撤路径 → 回撤仍有分布
        pnls = _interleaved_losses(10, 20)

        # Act
        result = run_mc_robustness(pnls, INITIAL_CASH, n_scenarios=1000, method="shuffle", seed=7)
        dd = _get_metric(result, "max_drawdown_pct")

        # Assert
        assert dd.std > 0.0


class TestMetricDirection:
    """_METRIC_DIRECTION 方向语义（p 值方向）。"""

    def test_all_metrics_are_higher_is_better(self):
        # Arrange / Act / Assert: 含 max_drawdown_pct=True（回撤越接近 0 越好）
        assert _METRIC_DIRECTION["max_drawdown_pct"] is True
        assert all(v is True for v in _METRIC_DIRECTION.values())

    def test_shallow_original_drawdown_yields_small_p_value(self):
        # Arrange: 原始把亏损均匀铺开 → 回撤最浅，优于绝大多数重采样
        pnls = _interleaved_losses(12, 24)

        # Act
        result = run_mc_robustness(pnls, INITIAL_CASH, n_scenarios=4000, method="bootstrap", seed=42)
        dd = _get_metric(result, "max_drawdown_pct")

        # Assert: higher_is_better=True → p=mean(values>=original) 应很小（原始最优）
        # 原始回撤优于约 98% 重采样 → p≈0.02，5% 显著
        assert 0.0 < dd.p_value < ALPHA_5_PERCENT
        assert dd.is_significant_5pct is True

    def test_deep_original_drawdown_yields_large_p_value(self):
        # Arrange: 尾部堆叠 12 连续亏损 → 原始回撤最深（最差）
        pnls = [2.0] * 24 + [-1.0] * 12

        # Act
        result = run_mc_robustness(pnls, INITIAL_CASH, n_scenarios=4000, method="bootstrap", seed=42)
        dd = _get_metric(result, "max_drawdown_pct")

        # Assert: 原始最差 → 几乎所有重采样都 ≥ 原始 → p≈1，不显著
        # 若方向写反（用 <=）此处会得到极小 p，故该断言可区分方向正确性
        assert dd.p_value > 0.5
        assert dd.is_significant_5pct is False

    def test_prob_profit_and_beat_are_valid_ratios(self):
        # Arrange
        pnls = _interleaved_losses(10, 20)

        # Act
        result = run_mc_robustness(pnls, INITIAL_CASH, n_scenarios=1000, method="bootstrap", seed=42)

        # Assert
        assert 0.0 <= result.prob_profit <= 1.0
        assert 0.0 <= result.prob_beat_original <= 1.0
        # 该组合总收益恒正 → 盈利概率为 1
        assert result.prob_profit == pytest.approx(1.0)


class TestBoundaries:
    """边界与输入校验。"""

    def test_empty_trades_raises(self):
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="交易笔数不足"):
            run_mc_robustness([], INITIAL_CASH)

    def test_single_trade_raises(self):
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="交易笔数不足"):
            run_mc_robustness([5.0], INITIAL_CASH)

    def test_below_min_trades_raises(self):
        # Arrange: MIN_TRADES-1 笔
        pnls = [1.0] * (MIN_TRADES - 1)

        # Act / Assert
        with pytest.raises(ValueError, match=f"至少需 {MIN_TRADES}"):
            run_mc_robustness(pnls, INITIAL_CASH)

    def test_exactly_min_trades_succeeds(self):
        # Arrange: 恰好 MIN_TRADES 笔应可运行
        pnls = [1.0, -0.5, 2.0, -1.0, 0.5]
        assert len(pnls) == MIN_TRADES

        # Act
        result = run_mc_robustness(pnls, INITIAL_CASH, n_scenarios=200, method="bootstrap", seed=1)

        # Assert
        assert result.n_trades == MIN_TRADES

    def test_zero_scenarios_raises(self):
        # Arrange
        pnls = _interleaved_losses(6, 10)

        # Act / Assert
        with pytest.raises(ValueError, match="场景数"):
            run_mc_robustness(pnls, INITIAL_CASH, n_scenarios=0)

    def test_unknown_method_raises(self):
        # Arrange
        pnls = _interleaved_losses(6, 10)

        # Act / Assert
        with pytest.raises(ValueError, match="未知方法"):
            run_mc_robustness(pnls, INITIAL_CASH, method="jackknife")

    def test_accepts_numpy_array_input(self):
        # Arrange
        pnls = np.array(_interleaved_losses(8, 12), dtype=float)

        # Act
        result = run_mc_robustness(pnls, INITIAL_CASH, n_scenarios=300, method="bootstrap", seed=5)

        # Assert
        assert result.n_trades == 20
