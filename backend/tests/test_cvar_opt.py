"""CVaR / CDaR 尾部风险优化（cvar_opt.py）单元测试

覆盖：
- min_cvar_weights / min_cdar_weights 权重和为 1、全部落在 [0, 1]
- beta 边界与形状校验（beta∈(0,1)、≥2 资产、≥2 期、二维矩阵）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.engine.portfolio.cvar_opt import (
    DEFAULT_BETA,
    min_cdar_weights,
    min_cvar_weights,
)

SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN"]


def _make_returns_matrix(n_days: int = 250, seed: int = 42) -> np.ndarray:
    """生成 250 日 4 资产日收益矩阵 (T × N)。"""
    rng = np.random.default_rng(seed)
    cols = []
    for _ in SYMBOLS:
        cols.append(rng.normal(0.0005, 0.015, n_days))
    prices = 100.0 * np.cumprod(1 + np.column_stack(cols), axis=0)
    returns = pd.DataFrame(prices).pct_change().dropna().to_numpy()
    return returns


class TestMinCvarWeights:
    def test_weights_sum_to_one(self) -> None:
        # Arrange
        returns = _make_returns_matrix()

        # Act
        weights = min_cvar_weights(returns)

        # Assert
        assert abs(weights.sum() - 1.0) < 1e-6

    def test_weights_within_unit_interval(self) -> None:
        # Arrange
        returns = _make_returns_matrix()

        # Act
        weights = min_cvar_weights(returns)

        # Assert：long-only 上限 1
        assert (weights >= -1e-9).all()
        assert (weights <= 1.0 + 1e-9).all()

    def test_weights_length_matches_assets(self) -> None:
        # Arrange
        returns = _make_returns_matrix()

        # Act
        weights = min_cvar_weights(returns)

        # Assert
        assert len(weights) == len(SYMBOLS)

    @pytest.mark.parametrize("beta", [0.90, 0.95, 0.99])
    def test_various_beta_levels_solve(self, beta: float) -> None:
        # Arrange
        returns = _make_returns_matrix()

        # Act
        weights = min_cvar_weights(returns, beta=beta)

        # Assert
        assert abs(weights.sum() - 1.0) < 1e-6

    def test_default_beta_is_used(self) -> None:
        # Arrange / Act：默认 beta 与显式默认一致
        returns = _make_returns_matrix()
        explicit = min_cvar_weights(returns, beta=DEFAULT_BETA)
        implicit = min_cvar_weights(returns)

        # Assert
        assert np.allclose(explicit, implicit, atol=1e-6)


class TestMinCdarWeights:
    def test_weights_sum_to_one(self) -> None:
        # Arrange
        returns = _make_returns_matrix()

        # Act
        weights = min_cdar_weights(returns)

        # Assert
        assert abs(weights.sum() - 1.0) < 1e-6

    def test_weights_within_unit_interval(self) -> None:
        # Arrange
        returns = _make_returns_matrix()

        # Act
        weights = min_cdar_weights(returns)

        # Assert
        assert (weights >= -1e-9).all()
        assert (weights <= 1.0 + 1e-9).all()


class TestCvarValidation:
    @pytest.mark.parametrize("bad_beta", [0.0, 1.0, -0.1, 1.5])
    def test_beta_out_of_bounds_raises(self, bad_beta: float) -> None:
        # Arrange
        returns = _make_returns_matrix()

        # Act / Assert：beta 必须严格在 (0, 1)
        with pytest.raises(ValueError, match="beta"):
            min_cvar_weights(returns, beta=bad_beta)

    def test_single_asset_raises(self) -> None:
        # Arrange：仅 1 列
        returns = _make_returns_matrix()[:, :1]

        # Act / Assert
        with pytest.raises(ValueError, match="至少需要 2 个资产"):
            min_cvar_weights(returns)

    def test_too_few_periods_raises(self) -> None:
        # Arrange：仅 1 期收益
        returns = _make_returns_matrix()[:1, :]

        # Act / Assert
        with pytest.raises(ValueError, match="样本期太短"):
            min_cvar_weights(returns)

    def test_non_2d_matrix_raises(self) -> None:
        # Arrange：一维向量
        returns = _make_returns_matrix()[:, 0]

        # Act / Assert
        with pytest.raises(ValueError, match="二维"):
            min_cvar_weights(returns)

    def test_cdar_beta_out_of_bounds_raises(self) -> None:
        # Arrange
        returns = _make_returns_matrix()

        # Act / Assert
        with pytest.raises(ValueError, match="beta"):
            min_cdar_weights(returns, beta=1.0)
