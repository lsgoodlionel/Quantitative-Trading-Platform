"""组合优化器单元测试"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.risk.portfolio import (
    compute_rebalance,
    optimize_portfolio,
    PortfolioWeights,
)


def _make_prices(
    n_days: int = 60,
    symbols: list[str] | None = None,
    seed: int = 42,
) -> pd.DataFrame:
    """生成随机模拟价格序列。"""
    rng = np.random.default_rng(seed)
    syms = symbols or ["AAPL", "MSFT", "GOOGL"]
    data = {}
    for sym in syms:
        returns = rng.normal(0.0005, 0.015, n_days)
        prices = 100.0 * np.cumprod(1 + returns)
        data[sym] = prices
    return pd.DataFrame(data)


class TestEqualWeight:
    def test_equal_weight_sums_to_one(self) -> None:
        prices = _make_prices(60, ["AAPL", "MSFT", "GOOGL"])
        result = optimize_portfolio(prices, mode="equal_weight")
        total = sum(result.weights.values())
        assert abs(total - 1.0) < 1e-9

    def test_equal_weight_each_symbol(self) -> None:
        prices = _make_prices(60, ["AAPL", "MSFT"])
        result = optimize_portfolio(prices, mode="equal_weight")
        for w in result.weights.values():
            assert abs(w - 0.5) < 1e-9

    def test_equal_weight_returns_metrics(self) -> None:
        prices = _make_prices(60)
        result = optimize_portfolio(prices, mode="equal_weight")
        assert result.expected_return is not None
        assert result.expected_volatility is not None
        assert result.sharpe_ratio is not None


class TestRiskParity:
    def test_risk_parity_sums_to_one(self) -> None:
        prices = _make_prices(60, ["AAPL", "MSFT", "GOOGL"])
        result = optimize_portfolio(prices, mode="risk_parity")
        total = sum(result.weights.values())
        assert abs(total - 1.0) < 1e-9

    def test_risk_parity_all_positive(self) -> None:
        prices = _make_prices(60)
        result = optimize_portfolio(prices, mode="risk_parity")
        assert all(w >= 0 for w in result.weights.values())

    def test_risk_parity_lower_vol_higher_weight(self) -> None:
        """高波动标的应获得更低权重（逆波动率分配）。"""
        rng = np.random.default_rng(0)
        # STABLE 波动率低（0.005），VOLATILE 波动率高（0.03）
        stable = 100 * np.cumprod(1 + rng.normal(0, 0.005, 120))
        volatile = 100 * np.cumprod(1 + rng.normal(0, 0.03, 120))
        prices = pd.DataFrame({"STABLE": stable, "VOLATILE": volatile})

        result = optimize_portfolio(prices, mode="risk_parity")
        assert result.weights["STABLE"] > result.weights["VOLATILE"]


class TestInputValidation:
    def test_raises_for_single_symbol(self) -> None:
        prices = _make_prices(60, ["AAPL"])
        with pytest.raises(ValueError, match="at least 2"):
            optimize_portfolio(prices, mode="equal_weight")

    def test_raises_for_insufficient_history(self) -> None:
        prices = _make_prices(10, ["AAPL", "MSFT"])
        with pytest.raises(ValueError, match="at least 30"):
            optimize_portfolio(prices, mode="equal_weight")

    def test_raises_for_empty_prices(self) -> None:
        prices = pd.DataFrame()
        with pytest.raises(ValueError):
            optimize_portfolio(prices, mode="equal_weight")


class TestComputeRebalance:
    def test_no_orders_when_balanced(self) -> None:
        orders = compute_rebalance(
            current_positions={"AAPL": 50_000, "MSFT": 50_000},
            target_weights={"AAPL": 0.5, "MSFT": 0.5},
            portfolio_value=100_000,
        )
        # 权重已对齐，不需要交易（delta_value < min_trade_value）
        assert len(orders) == 0

    def test_buy_order_generated_for_new_position(self) -> None:
        orders = compute_rebalance(
            current_positions={"AAPL": 100_000},
            target_weights={"AAPL": 0.5, "MSFT": 0.5},
            portfolio_value=100_000,
            min_trade_value=100.0,
        )
        symbols = {o.symbol for o in orders}
        assert "MSFT" in symbols
        msft_order = next(o for o in orders if o.symbol == "MSFT")
        assert msft_order.delta_value > 0  # 买入

    def test_sell_order_generated_for_overweight(self) -> None:
        orders = compute_rebalance(
            current_positions={"AAPL": 80_000, "MSFT": 20_000},
            target_weights={"AAPL": 0.5, "MSFT": 0.5},
            portfolio_value=100_000,
            min_trade_value=100.0,
        )
        aapl_order = next((o for o in orders if o.symbol == "AAPL"), None)
        assert aapl_order is not None
        assert aapl_order.delta_value < 0  # 卖出

    def test_small_deltas_filtered_by_min_trade_value(self) -> None:
        orders = compute_rebalance(
            current_positions={"AAPL": 50_100, "MSFT": 49_900},
            target_weights={"AAPL": 0.5, "MSFT": 0.5},
            portfolio_value=100_000,
            min_trade_value=500.0,  # 偏差 100，低于 500，应过滤
        )
        assert len(orders) == 0

    def test_sell_orders_come_before_buy_orders(self) -> None:
        """先卖后买，确保现金足够。"""
        orders = compute_rebalance(
            current_positions={"AAPL": 80_000, "MSFT": 0, "GOOGL": 20_000},
            target_weights={"AAPL": 0.33, "MSFT": 0.33, "GOOGL": 0.34},
            portfolio_value=100_000,
            min_trade_value=100.0,
        )
        sell_indices = [i for i, o in enumerate(orders) if o.delta_value < 0]
        buy_indices = [i for i, o in enumerate(orders) if o.delta_value > 0]
        if sell_indices and buy_indices:
            assert max(sell_indices) < min(buy_indices)

    def test_exit_position_when_target_zero(self) -> None:
        orders = compute_rebalance(
            current_positions={"AAPL": 60_000, "MSFT": 40_000},
            target_weights={"AAPL": 1.0},  # MSFT 目标权重 0（清仓）
            portfolio_value=100_000,
            min_trade_value=100.0,
        )
        msft_order = next((o for o in orders if o.symbol == "MSFT"), None)
        assert msft_order is not None
        assert msft_order.delta_value < 0

    def test_weights_in_result(self) -> None:
        orders = compute_rebalance(
            current_positions={"AAPL": 80_000, "MSFT": 20_000},
            target_weights={"AAPL": 0.5, "MSFT": 0.5},
            portfolio_value=100_000,
            min_trade_value=100.0,
        )
        for o in orders:
            assert 0.0 <= o.current_weight <= 1.0
            assert 0.0 <= o.target_weight <= 1.0
