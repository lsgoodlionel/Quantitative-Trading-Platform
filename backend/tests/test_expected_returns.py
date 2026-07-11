"""预期收益估计器单元测试

覆盖：
- mean / ema / capm 返回形状正确的 Series（index=symbol）
- CAPM 对「市场代理自身」β≈1
- 收益数据 <2 行时抛 ValueError
- 未知方法 / 非 DataFrame 校验
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.engine.portfolio.expected_returns import (
    ReturnsModel,
    capm_return,
    ema_historical_return,
    expected_returns,
    mean_historical_return,
)


def _make_prices(
    n_days: int = 120,
    symbols: list[str] | None = None,
    seed: int = 42,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    syms = symbols or ["AAPL", "MSFT", "GOOGL"]
    data = {}
    for sym in syms:
        returns = rng.normal(0.0005, 0.015, n_days)
        data[sym] = 100.0 * np.cumprod(1 + returns)
    return pd.DataFrame(data)


class TestShapes:
    def test_mean_historical_shape(self) -> None:
        # Arrange
        symbols = ["AAPL", "MSFT", "GOOGL"]
        prices = _make_prices(120, symbols)

        # Act
        mu = mean_historical_return(prices)

        # Assert
        assert isinstance(mu, pd.Series)
        assert list(mu.index) == symbols
        assert mu.notna().all()

    def test_ema_historical_shape(self) -> None:
        # Arrange
        symbols = ["AAPL", "MSFT"]
        prices = _make_prices(120, symbols)

        # Act
        mu = ema_historical_return(prices, span=60)

        # Assert
        assert list(mu.index) == symbols
        assert mu.notna().all()

    def test_capm_shape(self) -> None:
        # Arrange
        symbols = ["AAPL", "MSFT", "GOOGL"]
        prices = _make_prices(120, symbols)

        # Act
        mu = capm_return(prices)

        # Assert
        assert list(mu.index) == symbols
        assert mu.notna().all()

    def test_dispatcher_all_methods(self) -> None:
        # Arrange
        prices = _make_prices(120, ["AAPL", "MSFT"])

        # Act / Assert
        for method in (
            ReturnsModel.MEAN_HISTORICAL,
            ReturnsModel.EMA_HISTORICAL,
            ReturnsModel.CAPM,
        ):
            mu = expected_returns(prices, method=method)
            assert list(mu.index) == ["AAPL", "MSFT"]


class TestCapmBeta:
    def test_market_proxy_self_beta_is_one(self) -> None:
        # Arrange: 用一列价格自身作为市场代理，β 应 ≈ 1
        rng = np.random.default_rng(7)
        n = 200
        mkt_returns = rng.normal(0.0004, 0.012, n)
        mkt_prices = 100.0 * np.cumprod(1 + mkt_returns)
        # 资产 X 与市场完全同步（就是市场本身）
        prices = pd.DataFrame({"X": mkt_prices})
        market = pd.DataFrame({"MKT": mkt_prices})

        # Act
        mu = capm_return(prices, market_prices=market)

        # β=1 时 implied = rf + 1*(market_annual - rf) = market_annual
        market_annual = float(
            (1 + pd.Series(mkt_prices).pct_change().dropna()).prod()
            ** (252 / (n - 1))
            - 1
        )

        # Assert: 隐含收益 ≈ 年化市场收益（即 β≈1）
        assert mu["X"] == pytest.approx(market_annual, rel=1e-3)

    def test_equal_weight_proxy_average_beta_near_one(self) -> None:
        # Arrange: 未提供 market_prices → 等权组合代理市场
        symbols = ["A", "B", "C", "D"]
        prices = _make_prices(200, symbols)

        # Act
        mu = capm_return(prices)
        returns = prices.pct_change().dropna()
        market_ret = returns.mean(axis=1)
        market_var = float(market_ret.var())
        betas = returns.apply(lambda c: c.cov(market_ret) / market_var)

        # Assert: 等权代理下 β 的均值 ≈ 1
        assert float(betas.mean()) == pytest.approx(1.0, abs=1e-6)

    def test_zero_variance_market_falls_back_to_rf(self) -> None:
        # Arrange: 恒定价格 → 收益全 0 → 市场无波动
        flat = pd.DataFrame(
            {"A": [100.0] * 10, "B": [100.0] * 10}
        )

        # Act
        mu = capm_return(flat, risk_free_rate=0.05)

        # Assert: 退化为无风险利率
        assert mu["A"] == pytest.approx(0.05)
        assert mu["B"] == pytest.approx(0.05)


class TestValidation:
    def test_too_few_rows_raises(self) -> None:
        # Arrange: 仅 2 行价格 → 1 行收益 (<2) → 抛错
        prices = pd.DataFrame({"A": [100.0, 101.0], "B": [50.0, 49.0]})

        # Act / Assert
        with pytest.raises(ValueError):
            mean_historical_return(prices)

    def test_too_few_rows_raises_via_dispatcher(self) -> None:
        prices = pd.DataFrame({"A": [100.0, 101.0], "B": [50.0, 49.0]})
        with pytest.raises(ValueError):
            expected_returns(prices, method="ema_historical")

    def test_non_dataframe_raises(self) -> None:
        with pytest.raises(ValueError):
            mean_historical_return([100, 101, 102])  # type: ignore[arg-type]

    def test_unknown_method_raises(self) -> None:
        prices = _make_prices(60, ["A", "B"])
        with pytest.raises(ValueError):
            expected_returns(prices, method="bogus")
