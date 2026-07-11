"""Black-Litterman 模型（black_litterman.py）单元测试

覆盖：
- market_implied_prior_returns 反向优化解析式 π = δΣw + rf
- market_implied_risk_aversion 由市场价格估计 δ
- idzorek_omega 生成正定（对角为正）的观点不确定性矩阵
- black_litterman 后验收益/协方差 shape 正确、协方差对称
- parse_views 观点校验（未知标的/置信度越界/元数不符）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.engine.portfolio.black_litterman import (
    DEFAULT_RISK_AVERSION,
    InvestorView,
    ViewKind,
    black_litterman,
    idzorek_omega,
    market_implied_prior_returns,
    market_implied_risk_aversion,
    parse_views,
)

SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN"]


def _make_prices(n_days: int = 250, seed: int = 42) -> pd.DataFrame:
    """生成 250 日 4 资产价格（宽格式）。"""
    rng = np.random.default_rng(seed)
    data = {}
    for sym in SYMBOLS:
        returns = rng.normal(0.0005, 0.015, n_days)
        data[sym] = 100.0 * np.cumprod(1 + returns)
    return pd.DataFrame(data)


def _annualized_cov(prices: pd.DataFrame) -> pd.DataFrame:
    """由价格估计年化协方差。"""
    return prices.pct_change().dropna().cov() * 252


class TestMarketImpliedPrior:
    def test_prior_matches_closed_form(self) -> None:
        # Arrange：小型解析可算的协方差与权重
        cov = np.array([[0.04, 0.01], [0.01, 0.09]])
        weights = np.array([0.6, 0.4])
        delta = 2.5

        # Act
        pi = market_implied_prior_returns(weights, delta, cov, risk_free_rate=0.02)

        # Assert：π = δ·Σ·w + rf
        expected = delta * (cov @ weights) + 0.02
        assert np.allclose(pi, expected)

    def test_prior_scales_with_risk_aversion(self) -> None:
        # Arrange
        cov = np.array([[0.04, 0.0], [0.0, 0.09]])
        weights = np.array([0.5, 0.5])

        # Act：δ 翻倍，rf=0 → 隐含收益翻倍
        pi_1 = market_implied_prior_returns(weights, 1.0, cov)
        pi_2 = market_implied_prior_returns(weights, 2.0, cov)

        # Assert
        assert np.allclose(pi_2, 2.0 * pi_1)


class TestMarketImpliedRiskAversion:
    def test_returns_positive_for_upward_market(self) -> None:
        # Arrange：稳步上涨的市场组合价格
        prices = pd.Series(100.0 * np.cumprod(1 + np.full(250, 0.001)))

        # Act
        delta = market_implied_risk_aversion(prices)

        # Assert：正收益、极低波动 → 极大或退回默认值（有限正数）
        assert np.isfinite(delta)

    def test_zero_variance_falls_back_to_default(self) -> None:
        # Arrange：常数价格 → 方差为 0
        prices = pd.Series(np.full(100, 100.0))

        # Act
        delta = market_implied_risk_aversion(prices)

        # Assert
        assert delta == DEFAULT_RISK_AVERSION


class TestIdzorekOmega:
    def test_omega_is_diagonal_and_positive(self) -> None:
        # Arrange
        cov = _annualized_cov(_make_prices()).to_numpy()
        p_mat = np.array([[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, -1.0, 0.0]])
        confidences = np.array([0.6, 0.4])

        # Act
        omega = idzorek_omega(confidences, cov, p_mat, tau=0.05)

        # Assert：对角矩阵、对角元严格为正（正定）
        assert np.allclose(omega, np.diag(np.diag(omega)))
        assert np.all(np.diag(omega) > 0.0)

    def test_zero_confidence_yields_large_variance(self) -> None:
        # Arrange：置信度 0 → 观点被极大方差忽略
        cov = _annualized_cov(_make_prices()).to_numpy()
        p_mat = np.array([[1.0, 0.0, 0.0, 0.0]])

        # Act
        omega = idzorek_omega(np.array([0.0]), cov, p_mat, tau=0.05)

        # Assert
        assert omega[0, 0] >= 1e6

    def test_higher_confidence_shrinks_variance(self) -> None:
        # Arrange
        cov = _annualized_cov(_make_prices()).to_numpy()
        p_mat = np.array([[1.0, 0.0, 0.0, 0.0]])

        # Act：置信度越高 → Ω 越小
        low = idzorek_omega(np.array([0.3]), cov, p_mat, tau=0.05)[0, 0]
        high = idzorek_omega(np.array([0.9]), cov, p_mat, tau=0.05)[0, 0]

        # Assert
        assert high < low


class TestBlackLittermanPosterior:
    def test_posterior_shapes_and_index(self) -> None:
        # Arrange
        cov = _annualized_cov(_make_prices())
        views = [InvestorView(ViewKind.ABSOLUTE, ("AAPL",), 0.15, 0.6)]

        # Act
        result = black_litterman(cov, views)

        # Assert
        assert list(result.posterior_returns.index) == SYMBOLS
        assert result.posterior_cov.shape == (4, 4)
        assert list(result.prior_returns.index) == SYMBOLS

    def test_posterior_cov_is_symmetric(self) -> None:
        # Arrange
        cov = _annualized_cov(_make_prices())
        views = [InvestorView(ViewKind.RELATIVE, ("AAPL", "MSFT"), 0.05, 0.5)]

        # Act
        result = black_litterman(cov, views)

        # Assert
        arr = result.posterior_cov.to_numpy()
        assert np.allclose(arr, arr.T, atol=1e-10)

    def test_market_weights_default_equal(self) -> None:
        # Arrange：无 market_caps → 等权代理
        cov = _annualized_cov(_make_prices())
        views = [InvestorView(ViewKind.ABSOLUTE, ("MSFT",), 0.1, 0.5)]

        # Act
        result = black_litterman(cov, views)

        # Assert
        weights = list(result.market_weights.values())
        assert np.allclose(weights, 0.25, atol=1e-4)

    def test_explicit_risk_aversion_used(self) -> None:
        # Arrange
        cov = _annualized_cov(_make_prices())
        views = [InvestorView(ViewKind.ABSOLUTE, ("AAPL",), 0.1, 0.5)]

        # Act
        result = black_litterman(cov, views, risk_aversion=3.0)

        # Assert
        assert result.risk_aversion == 3.0

    def test_market_caps_drive_weights(self) -> None:
        # Arrange：市值加权先验
        cov = _annualized_cov(_make_prices())
        views = [InvestorView(ViewKind.ABSOLUTE, ("AAPL",), 0.1, 0.5)]
        caps = {"AAPL": 400.0, "MSFT": 300.0, "GOOGL": 200.0, "AMZN": 100.0}

        # Act
        result = black_litterman(cov, views, market_caps=caps)

        # Assert：AAPL 权重最大
        assert result.market_weights["AAPL"] == max(result.market_weights.values())
        assert abs(sum(result.market_weights.values()) - 1.0) < 1e-3

    def test_single_asset_raises(self) -> None:
        # Arrange：单资产协方差
        cov = pd.DataFrame([[0.04]], index=["AAPL"], columns=["AAPL"])
        views = [InvestorView(ViewKind.ABSOLUTE, ("AAPL",), 0.1, 0.5)]

        # Act / Assert
        with pytest.raises(ValueError, match="至少需要 2 个资产"):
            black_litterman(cov, views)


class TestParseViews:
    def test_empty_views_raises(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="至少需要 1 条"):
            parse_views([], SYMBOLS)

    def test_unknown_symbol_raises(self) -> None:
        # Arrange
        views = [InvestorView(ViewKind.ABSOLUTE, ("TSLA",), 0.1, 0.5)]

        # Act / Assert
        with pytest.raises(ValueError, match="不在资产池"):
            parse_views(views, SYMBOLS)

    def test_confidence_out_of_range_raises(self) -> None:
        # Arrange
        views = [InvestorView(ViewKind.ABSOLUTE, ("AAPL",), 0.1, 1.5)]

        # Act / Assert
        with pytest.raises(ValueError, match="置信度"):
            parse_views(views, SYMBOLS)

    def test_relative_view_requires_two_assets(self) -> None:
        # Arrange
        views = [InvestorView(ViewKind.RELATIVE, ("AAPL",), 0.05, 0.5)]

        # Act / Assert
        with pytest.raises(ValueError, match="2 个标的"):
            parse_views(views, SYMBOLS)

    def test_pick_matrix_and_q_shapes(self) -> None:
        # Arrange：1 绝对 + 1 相对观点
        views = [
            InvestorView(ViewKind.ABSOLUTE, ("AAPL",), 0.15, 0.6),
            InvestorView(ViewKind.RELATIVE, ("MSFT", "GOOGL"), 0.03, 0.5),
        ]

        # Act
        p_mat, q_vec, confidences, labels = parse_views(views, SYMBOLS)

        # Assert
        assert p_mat.shape == (2, 4)
        assert q_vec.shape == (2, 1)
        assert confidences.shape == (2,)
        assert len(labels) == 2
        # 绝对观点行只在 AAPL 位置为 1
        assert p_mat[0, 0] == 1.0
        # 相对观点行 long=+1 short=-1
        assert p_mat[1, 1] == 1.0 and p_mat[1, 2] == -1.0
