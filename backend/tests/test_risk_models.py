"""风险模型（协方差估计器）单元测试

覆盖：
- sample_cov / ledoit_wolf / exp_cov / semicovariance 经 risk_matrix 修复后
  返回对称正半定矩阵（min eigenvalue ≥ -1e-8）、shape 正确
- fix_nonpositive_semidefinite 把非 PSD 矩阵修复为 PSD
- 输入校验（<2 资产、非 DataFrame、未知方法）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.engine.portfolio.risk_models import (
    RiskModel,
    exp_cov,
    fix_nonpositive_semidefinite,
    ledoit_wolf_cov,
    risk_matrix,
    sample_cov,
    semicovariance,
)

PSD_TOL = -1e-8


def _make_prices(
    n_days: int = 120,
    symbols: list[str] | None = None,
    seed: int = 42,
) -> pd.DataFrame:
    """生成随机模拟价格序列（宽格式）。"""
    rng = np.random.default_rng(seed)
    syms = symbols or ["AAPL", "MSFT", "GOOGL", "AMZN"]
    data = {}
    for sym in syms:
        returns = rng.normal(0.0005, 0.015, n_days)
        data[sym] = 100.0 * np.cumprod(1 + returns)
    return pd.DataFrame(data)


def _min_eigenvalue(cov: pd.DataFrame) -> float:
    return float(np.min(np.linalg.eigvalsh(cov.to_numpy())))


def _is_symmetric(cov: pd.DataFrame) -> bool:
    arr = cov.to_numpy()
    return bool(np.allclose(arr, arr.T, atol=1e-10))


ALL_METHODS = [
    RiskModel.SAMPLE_COV,
    RiskModel.LEDOIT_WOLF,
    RiskModel.EXP_COV,
    RiskModel.SEMICOVARIANCE,
]


class TestRiskMatrixPSD:
    @pytest.mark.parametrize("method", ALL_METHODS)
    def test_returns_symmetric_psd(self, method: RiskModel) -> None:
        # Arrange
        symbols = ["AAPL", "MSFT", "GOOGL", "AMZN"]
        prices = _make_prices(120, symbols)

        # Act
        cov = risk_matrix(prices, method=method)

        # Assert: 对称
        assert _is_symmetric(cov)
        # Assert: 正半定
        assert _min_eigenvalue(cov) >= PSD_TOL

    @pytest.mark.parametrize("method", ALL_METHODS)
    def test_shape_matches_symbols(self, method: RiskModel) -> None:
        # Arrange
        symbols = ["AAPL", "MSFT", "GOOGL"]
        prices = _make_prices(90, symbols)

        # Act
        cov = risk_matrix(prices, method=method)

        # Assert
        assert cov.shape == (len(symbols), len(symbols))
        assert list(cov.index) == symbols
        assert list(cov.columns) == symbols

    def test_string_method_accepted(self) -> None:
        # Arrange
        prices = _make_prices(60, ["AAPL", "MSFT"])

        # Act
        cov = risk_matrix(prices, method="ledoit_wolf")

        # Assert
        assert cov.shape == (2, 2)
        assert _min_eigenvalue(cov) >= PSD_TOL


class TestEstimatorsDirectly:
    def test_sample_cov_shape_and_symmetry(self) -> None:
        prices = _make_prices(80, ["A", "B", "C"])
        cov = sample_cov(prices)
        assert cov.shape == (3, 3)
        assert _is_symmetric(cov)

    def test_semicovariance_is_downside_only(self) -> None:
        # Arrange: 半协方差对角线非负
        prices = _make_prices(80, ["A", "B", "C"])

        # Act
        cov = semicovariance(prices)

        # Assert: 对角（下行方差）≥ 0
        assert (np.diag(cov.to_numpy()) >= -1e-12).all()

    def test_exp_cov_weights_recent_more(self) -> None:
        # Arrange
        prices = _make_prices(100, ["A", "B"])

        # Act
        cov = exp_cov(prices, span=30)

        # Assert
        assert cov.shape == (2, 2)
        assert _is_symmetric(cov)

    def test_ledoit_wolf_shrinks_toward_target(self) -> None:
        # Arrange
        prices = _make_prices(60, ["A", "B", "C", "D"])

        # Act
        cov = ledoit_wolf_cov(prices)

        # Assert: 收缩估计天然 PSD
        assert _min_eigenvalue(cov) >= PSD_TOL


class TestFixNonPositiveSemidefinite:
    def test_repairs_non_psd_matrix(self) -> None:
        # Arrange: 构造带负特征值的对称矩阵
        bad = pd.DataFrame(
            [[1.0, 2.0], [2.0, 1.0]],  # 特征值 3 与 -1
            index=["A", "B"],
            columns=["A", "B"],
        )
        assert _min_eigenvalue(bad) < 0  # 前提：确实非 PSD

        # Act
        fixed = fix_nonpositive_semidefinite(bad, fix_method="spectral")

        # Assert
        assert _min_eigenvalue(fixed) >= PSD_TOL
        assert _is_symmetric(fixed)

    def test_diag_method_repairs(self) -> None:
        # Arrange
        bad = pd.DataFrame(
            [[1.0, 2.0], [2.0, 1.0]],
            index=["A", "B"],
            columns=["A", "B"],
        )

        # Act
        fixed = fix_nonpositive_semidefinite(bad, fix_method="diag")

        # Assert
        assert _min_eigenvalue(fixed) >= PSD_TOL

    def test_already_psd_passthrough(self) -> None:
        # Arrange: 单位阵已是 PSD
        good = pd.DataFrame(
            np.eye(3), index=["A", "B", "C"], columns=["A", "B", "C"]
        )

        # Act
        fixed = fix_nonpositive_semidefinite(good)

        # Assert
        np.testing.assert_allclose(fixed.to_numpy(), np.eye(3))

    def test_unknown_fix_method_raises(self) -> None:
        bad = pd.DataFrame(
            [[1.0, 2.0], [2.0, 1.0]], index=["A", "B"], columns=["A", "B"]
        )
        with pytest.raises(ValueError):
            fix_nonpositive_semidefinite(bad, fix_method="bogus")


class TestValidation:
    def test_single_asset_raises(self) -> None:
        prices = _make_prices(60, ["A"])
        with pytest.raises(ValueError):
            risk_matrix(prices)

    def test_non_dataframe_raises(self) -> None:
        with pytest.raises(ValueError):
            sample_cov([1, 2, 3])  # type: ignore[arg-type]

    def test_unknown_method_raises(self) -> None:
        prices = _make_prices(60, ["A", "B"])
        with pytest.raises(ValueError):
            risk_matrix(prices, method="not_a_model")
