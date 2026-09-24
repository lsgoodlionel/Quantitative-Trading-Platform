"""面板算子（panel_ops）单元测试 —— Alpha101 的算子底座

Alpha101 的正确性完全建立在这些原语的语义上：窗口方向、权重方向、
条件算子的真假分支、NaN 是否被当成 False。任何一处反了，几十个 alpha
一起悄悄算错还照样输出「合理」的数字。所以这里全部用手算小样本钉死。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.quant.factor_lib.panel_ops import (
    log,
    lt,
    pow1,
    pow2,
    quesval,
    quesval2,
    sign,
    ts_argmax,
    ts_argmin,
    ts_corr,
    ts_cov,
    ts_decay_linear,
    ts_delay,
    ts_delta,
    ts_greater,
    ts_less,
    ts_product,
    ts_rank,
    ts_std,
    ts_sum,
)

_IDX = pd.Index([f"d{i}" for i in range(6)], name="datetime")
#: 手算用序列：[1, 5, 3, 2, 9, 4]
_A = pd.DataFrame({"X": [1.0, 5.0, 3.0, 2.0, 9.0, 4.0]}, index=_IDX)


def _col(frame: pd.DataFrame) -> list[float]:
    return [float(v) for v in frame["X"].to_numpy(dtype=float)]


class TestRollingWindowDirection:
    def test_ts_argmax_counts_from_window_start(self) -> None:
        # 窗口 [1,5,3]→位置2；[5,3,2]→1；[3,2,9]→3；[2,9,4]→2
        result = _col(ts_argmax(_A, 3))
        assert result[2:] == [2.0, 1.0, 3.0, 2.0]
        assert np.isnan(result[0])
        assert np.isnan(result[1])

    def test_ts_argmin_counts_from_window_start(self) -> None:
        # 窗口 [1,5,3]→1；[5,3,2]→3；[3,2,9]→2；[2,9,4]→1
        assert _col(ts_argmin(_A, 3))[2:] == [1.0, 3.0, 2.0, 1.0]

    def test_ts_rank_ranks_the_latest_value_in_the_window(self) -> None:
        # [1,5,3] 末值 3 排第 2/3；[5,3,2] 末值 2 排 1/3；[3,2,9]→3/3；[2,9,4]→2/3
        assert _col(ts_rank(_A, 3))[2:] == pytest.approx([2 / 3, 1 / 3, 1.0, 2 / 3])

    def test_ts_decay_linear_weights_the_most_recent_bar_heaviest(self) -> None:
        """权重方向是 Alpha101 的语义要害。

        原文 `decay_linear(x, d)` 的权重是 d, d-1, …, 1 由近及远递减，
        即**最近一根 bar 权重最大**。移植来源 vnpy 的实现把最大权重给了最老的
        一根（`range(window, 0, -1)` 乘在窗口正序上），与原文相反；这里按原文实现。
        """
        # 窗口 [1,5,3]，权重 (1,2,3)/6 ⇒ (1·1 + 2·5 + 3·3)/6 = 20/6
        # 窗口 [2,9,4]，权重 (1,2,3)/6 ⇒ (1·2 + 2·9 + 3·4)/6 = 32/6
        result = _col(ts_decay_linear(_A, 3))
        assert result[2] == pytest.approx(20 / 6)
        assert result[5] == pytest.approx(32 / 6)
        # 若权重方向反了，末窗口会是 (3·2 + 2·9 + 1·4)/6 = 28/6
        assert result[5] != pytest.approx(28 / 6)

    def test_ts_delay_and_delta_are_consistent(self) -> None:
        assert _col(ts_delta(_A, 2))[2:] == pytest.approx([2.0, -3.0, 6.0, 2.0])
        assert _col(ts_delay(_A, 1))[1:] == [1.0, 5.0, 3.0, 2.0, 9.0]

    def test_ts_sum_and_product(self) -> None:
        assert _col(ts_sum(_A, 3))[2:] == pytest.approx([9.0, 10.0, 14.0, 15.0])
        assert _col(ts_product(_A, 3))[2:] == pytest.approx([15.0, 30.0, 54.0, 72.0])

    def test_ts_std_uses_population_ddof_zero(self) -> None:
        """ddof 不是细节：alpha18 把 std 与价差相加、alpha21 拿 mean±std 当阈值，
        sqrt(n/(n-1)) 的常数偏差会真的改变排序与分支归属。移植来源用的是 ddof=0。
        """
        # 窗口 [1,5,3]：均值 3，总体方差 ((−2)²+2²+0²)/3 = 8/3
        result = _col(ts_std(_A, 3))
        assert result[2] == pytest.approx(np.sqrt(8 / 3))
        # 样本口径（ddof=1）会是 sqrt(4)=2，必须不是这个值
        assert result[2] != pytest.approx(2.0)

    def test_ts_cov_uses_population_ddof_zero(self) -> None:
        # 与 ts_std 同口径：总体协方差 = 样本协方差 × (n−1)/n
        other = pd.DataFrame({"X": [2.0, 4.0, 8.0, 1.0, 7.0, 5.0]}, index=_IDX)
        sample = _A["X"].rolling(3, min_periods=3).cov(other["X"])
        expected = (sample * (2 / 3)).to_numpy(dtype=float)
        np.testing.assert_allclose(
            ts_cov(_A, other, 3)["X"].to_numpy(dtype=float), expected, equal_nan=True
        )

    def test_windows_below_the_minimum_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="窗口"):
            ts_sum(_A, 0)
        with pytest.raises(ValueError, match="窗口"):
            ts_corr(_A, _A, 1)


class TestMathOperators:
    def test_sign_maps_zero_to_zero_and_keeps_nan(self) -> None:
        frame = pd.DataFrame({"X": [-2.0, 0.0, 3.0, np.nan]}, index=list("abcd"))
        result = _col(sign(frame))
        assert result[:3] == [-1.0, 0.0, 1.0]
        assert np.isnan(result[3])

    def test_pow1_preserves_the_sign_of_the_base(self) -> None:
        # 保号幂：sign(x)·|x|^e —— 负底数不产生 NaN
        frame = pd.DataFrame({"X": [-2.0, 0.0, 3.0, -1.0]}, index=list("abcd"))
        assert _col(pow1(frame, 2.0)) == pytest.approx([-4.0, 0.0, 9.0, -1.0])

    def test_pow2_follows_the_three_branch_rule(self) -> None:
        # base>0 → 正常求幂；base<0 且指数为整数 → -|base|^e；其余 → 0
        base = pd.DataFrame({"X": [-2.0, -2.0, 0.0, 4.0]}, index=list("abcd"))
        expo = pd.DataFrame({"X": [3.0, 0.5, 2.0, 0.5]}, index=list("abcd"))
        assert _col(pow2(base, expo)) == pytest.approx([-8.0, 0.0, 0.0, 2.0])

    def test_log_of_non_positive_is_nan_not_negative_infinity(self) -> None:
        frame = pd.DataFrame({"X": [np.e, 0.0, -1.0]}, index=list("abc"))
        result = _col(log(frame))
        assert result[0] == pytest.approx(1.0)
        assert np.isnan(result[1])
        assert np.isnan(result[2])


class TestConditionalOperators:
    def test_quesval_takes_the_true_branch_when_threshold_is_below_cond(self) -> None:
        # 语义（对齐 vnpy）：threshold < cond ? if_true : if_false
        cond = pd.DataFrame({"X": [0.5, -0.5, np.nan]}, index=list("abc"))
        result = _col(quesval(0.0, cond, 10.0, 20.0))
        assert result[:2] == [10.0, 20.0]
        assert np.isnan(result[2]), "条件为 NaN 时不能默默当成 False"

    def test_quesval2_uses_a_panel_threshold(self) -> None:
        threshold = pd.DataFrame({"X": [0.0, 0.0, 0.0]}, index=list("abc"))
        cond = pd.DataFrame({"X": [0.5, -0.5, np.nan]}, index=list("abc"))
        result = _col(quesval2(threshold, cond, 1.0, -1.0))
        assert result[:2] == [1.0, -1.0]
        assert np.isnan(result[2])

    def test_quesval_branches_may_themselves_be_panels(self) -> None:
        cond = pd.DataFrame({"X": [1.0, -1.0]}, index=list("ab"))
        yes = pd.DataFrame({"X": [7.0, 7.0]}, index=list("ab"))
        no = pd.DataFrame({"X": [9.0, 9.0]}, index=list("ab"))
        assert _col(quesval(0.0, cond, yes, no)) == [7.0, 9.0]

    def test_lt_indicator_propagates_nan_instead_of_returning_false(self) -> None:
        left = pd.DataFrame({"X": [1.0, 5.0, np.nan]}, index=list("abc"))
        right = pd.DataFrame({"X": [3.0, 3.0, 3.0]}, index=list("abc"))
        result = _col(lt(left, right))
        assert result[:2] == [1.0, 0.0]
        assert np.isnan(result[2])

    def test_ts_greater_and_ts_less_are_elementwise_extrema(self) -> None:
        left = pd.DataFrame({"X": [1.0, 5.0, np.nan]}, index=list("abc"))
        right = pd.DataFrame({"X": [3.0, 3.0, 3.0]}, index=list("abc"))
        assert _col(ts_greater(left, right))[:2] == [3.0, 5.0]
        assert _col(ts_less(left, right))[:2] == [1.0, 3.0]
        assert np.isnan(_col(ts_greater(left, right))[2])


class TestPairwiseRolling:
    def test_ts_corr_matches_single_series_correlation(self) -> None:
        # 宽表逐列相关必须等于把每列单独拿出来算
        rng = np.random.default_rng(0)
        a = pd.DataFrame(rng.normal(size=(20, 3)), columns=list("ABC"))
        b = pd.DataFrame(rng.normal(size=(20, 3)), columns=list("ABC"))

        result = ts_corr(a, b, 5)

        for col in "ABC":
            expected = a[col].rolling(5, min_periods=5).corr(b[col])
            np.testing.assert_allclose(
                result[col].to_numpy(dtype=float),
                expected.to_numpy(dtype=float),
                equal_nan=True,
            )

    def test_ts_corr_of_a_constant_series_is_nan_not_a_spurious_value(self) -> None:
        constant = pd.DataFrame({"X": [2.0] * 10}, index=range(10))
        varying = pd.DataFrame({"X": np.arange(10.0)}, index=range(10))
        assert ts_corr(constant, varying, 5)["X"].isna().all()
