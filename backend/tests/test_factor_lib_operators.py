"""滚动算子原语（operators.py）单元测试

用具有解析解的合成序列验证回归/相关/加权均值/分布位置类算子：
- 完美线性序列：slope=常数、rsquare≈1、residual≈0
- 常数序列：mad=0、corr 退化为 NaN
- 已知窗口：quantile / idxmax / idxmin 位置可手算

风格对齐 tests/test_risk_models.py：AAA 注释、numpy default_rng 固定种子、类分组。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.quant.factor_lib.operators import (
    ema,
    rolling_corr,
    rolling_cov,
    rolling_idxmax,
    rolling_idxmin,
    rolling_mad,
    rolling_quantile,
    rolling_resi,
    rolling_rsquare,
    rolling_slope,
    wma,
)

TOL = 1e-9


def _linear_series(intercept: float, step: float, n: int) -> pd.Series:
    """完美线性序列 s[i] = intercept + step·i。"""
    return pd.Series(intercept + step * np.arange(n, dtype=float))


class TestRegressionFamily:
    def test_slope_of_perfect_line_equals_step(self) -> None:
        # Arrange：每期上涨 2.0 的完美线性价格
        s = _linear_series(intercept=3.0, step=2.0, n=30)

        # Act
        result = rolling_slope(s, 10)

        # Assert：窗口内斜率恒等于步长，与窗口位置无关
        valid = result.dropna()
        assert len(valid) == 30 - 10 + 1
        assert np.allclose(valid.to_numpy(), 2.0, atol=1e-9)

    def test_slope_warmup_is_nan(self) -> None:
        # Arrange
        s = _linear_series(0.0, 1.0, 12)

        # Act
        result = rolling_slope(s, 5)

        # Assert：前 n-1 个为 NaN
        assert result.iloc[:4].isna().all()
        assert not np.isnan(result.iloc[4])

    def test_rsquare_of_perfect_line_is_one(self) -> None:
        # Arrange：完美线性 → 拟合优度为 1
        s = _linear_series(1.0, 0.5, 25)

        # Act
        result = rolling_rsquare(s, 8)

        # Assert
        assert np.allclose(result.dropna().to_numpy(), 1.0, atol=1e-9)

    def test_rsquare_constant_window_is_nan(self) -> None:
        # Arrange：常数序列 ss_tot=0 → NaN
        s = pd.Series(np.full(15, 7.0))

        # Act
        result = rolling_rsquare(s, 5)

        # Assert
        assert result.dropna().empty

    def test_resi_of_perfect_line_is_zero(self) -> None:
        # Arrange：完美线性拟合末端残差为 0
        s = _linear_series(-2.0, 3.0, 20)

        # Act
        result = rolling_resi(s, 6)

        # Assert
        assert np.allclose(result.dropna().to_numpy(), 0.0, atol=1e-9)

    def test_resi_nonzero_for_noisy_last_point(self) -> None:
        # Arrange：线性序列末尾抬高 → 末端残差为正
        values = list(_linear_series(0.0, 1.0, 10))
        values[-1] += 5.0
        s = pd.Series(values)

        # Act
        result = rolling_resi(s, 5)

        # Assert
        assert result.iloc[-1] > 0.0

    @pytest.mark.parametrize("fn", [rolling_slope, rolling_rsquare, rolling_resi])
    def test_regression_rejects_tiny_window(self, fn) -> None:
        # Arrange / Act / Assert：回归族窗口必须 ≥ 2
        with pytest.raises(ValueError):
            fn(pd.Series([1.0, 2.0, 3.0]), 1)


class TestPairRollingFamily:
    def test_corr_perfectly_correlated_is_one(self) -> None:
        # Arrange：b 为 a 的正线性变换 → 相关系数 1
        a = pd.Series(np.arange(30, dtype=float))
        b = 2.0 * a + 5.0

        # Act
        result = rolling_corr(a, b, 10)

        # Assert
        assert np.allclose(result.dropna().to_numpy(), 1.0, atol=1e-9)

    def test_corr_perfectly_anticorrelated_is_minus_one(self) -> None:
        # Arrange
        a = pd.Series(np.arange(30, dtype=float))
        b = -3.0 * a

        # Act
        result = rolling_corr(a, b, 10)

        # Assert
        assert np.allclose(result.dropna().to_numpy(), -1.0, atol=1e-9)

    def test_corr_constant_series_is_nan(self) -> None:
        # Arrange：常数序列标准差≈0 → 退化为 NaN
        a = pd.Series(np.arange(20, dtype=float))
        b = pd.Series(np.full(20, 4.0))

        # Act
        result = rolling_corr(a, b, 6)

        # Assert
        assert result.dropna().empty

    def test_cov_matches_numpy_sample_cov(self) -> None:
        # Arrange
        rng = np.random.default_rng(7)
        a = pd.Series(rng.normal(size=40))
        b = pd.Series(rng.normal(size=40))
        n = 12

        # Act
        result = rolling_cov(a, b, n)

        # Assert：末窗协方差与 numpy 样本协方差（ddof=1）一致
        window_a = a.iloc[-n:].to_numpy()
        window_b = b.iloc[-n:].to_numpy()
        expected = np.cov(window_a, window_b, ddof=1)[0, 1]
        assert abs(result.iloc[-1] - expected) < 1e-9

    def test_cov_of_series_with_itself_equals_variance(self) -> None:
        # Arrange
        a = pd.Series(np.arange(15, dtype=float))
        n = 5

        # Act
        result = rolling_cov(a, a, n)

        # Assert
        expected_var = float(np.var(np.arange(n), ddof=1))
        assert np.allclose(result.dropna().to_numpy(), expected_var, atol=1e-9)


class TestWeightedMeanFamily:
    def test_wma_constant_series_is_constant(self) -> None:
        # Arrange：常数序列的加权均值仍为该常数（权重归一）
        s = pd.Series(np.full(12, 9.0))

        # Act
        result = wma(s, 4)

        # Assert
        assert np.allclose(result.dropna().to_numpy(), 9.0, atol=1e-9)

    def test_wma_known_weights(self) -> None:
        # Arrange：窗口 [1,2,3]，权重 (1,2,3)/6 → 1/6+4/6+9/6=14/6
        s = pd.Series([1.0, 2.0, 3.0])

        # Act
        result = wma(s, 3)

        # Assert
        assert abs(result.iloc[-1] - (14.0 / 6.0)) < 1e-9

    def test_ema_constant_series_is_constant(self) -> None:
        # Arrange
        s = pd.Series(np.full(10, 3.0))

        # Act
        result = ema(s, 4)

        # Assert：常数输入 → EMA 恒为该常数，前 n-1 期 NaN
        assert result.iloc[:3].isna().all()
        assert np.allclose(result.dropna().to_numpy(), 3.0, atol=1e-9)

    def test_ema_matches_pandas_ewm(self) -> None:
        # Arrange
        rng = np.random.default_rng(11)
        s = pd.Series(rng.normal(size=20))

        # Act
        result = ema(s, 5)

        # Assert
        expected = s.ewm(span=5, adjust=False, min_periods=5).mean()
        pd.testing.assert_series_equal(result, expected)


class TestDistributionPositionFamily:
    def test_mad_constant_series_is_zero(self) -> None:
        # Arrange：常数序列离散度为 0
        s = pd.Series(np.full(15, 5.0))

        # Act
        result = rolling_mad(s, 5)

        # Assert
        assert np.allclose(result.dropna().to_numpy(), 0.0, atol=1e-12)

    def test_mad_known_value(self) -> None:
        # Arrange：窗口 [0,1,2,3,4] 均值 2，|dev|=(2+1+0+1+2)/5=1.2
        s = pd.Series(np.arange(5, dtype=float))

        # Act
        result = rolling_mad(s, 5)

        # Assert
        assert abs(result.iloc[-1] - 1.2) < 1e-9

    def test_quantile_rejects_out_of_range_q(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValueError):
            rolling_quantile(pd.Series(np.arange(10.0)), 5, 1.5)

    def test_quantile_median_of_known_window(self) -> None:
        # Arrange：窗口 [0..4] 中位数为 2
        s = pd.Series(np.arange(5, dtype=float))

        # Act
        result = rolling_quantile(s, 5, 0.5)

        # Assert
        assert abs(result.iloc[-1] - 2.0) < 1e-9

    def test_idxmax_returns_one_based_position(self) -> None:
        # Arrange：窗口最大值在末端 → 位置 = N
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])

        # Act
        result = rolling_idxmax(s, 5)

        # Assert
        assert result.iloc[-1] == 5.0

    def test_idxmin_returns_one_based_position(self) -> None:
        # Arrange：最小值在窗口首位 → 位置 = 1
        s = pd.Series([5.0, 4.0, 3.0, 2.0, 1.0])

        # Act：窗口 [5,4,3,2,1] 最小值 1 在第 5 位
        result = rolling_idxmin(s, 5)

        # Assert
        assert result.iloc[-1] == 5.0

    def test_idxmax_peak_in_middle(self) -> None:
        # Arrange：峰值位于窗口第 3 位
        s = pd.Series([1.0, 2.0, 9.0, 2.0, 1.0])

        # Act
        result = rolling_idxmax(s, 5)

        # Assert
        assert result.iloc[-1] == 3.0
