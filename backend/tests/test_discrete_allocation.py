"""离散配置（连续权重 → 整数股数）单元测试

覆盖：
- greedy + lp 预算守恒：sum(shares*price) + leftover ≈ total，leftover ≥ 0
- 跳过无价格标的后，剩余权重重新归一化
- 负权重 / NaN 权重 / 空输入 / 非正预算 抛错
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.engine.portfolio.discrete_allocation import (
    AllocationMethod,
    allocate,
    greedy_allocation,
    lp_allocation,
)

BUDGET_TOL = 1e-6


def _assert_budget_conserved(result, total: float) -> None:
    """预算守恒不变量：已配置 + 剩余 ≈ 预算，且剩余非负、无超支。"""
    assert result.leftover_cash >= 0.0
    # allocated_value 由整数股数算得，不得超过预算
    assert result.allocated_value <= total + 1e-6
    # 守恒（允许四舍五入误差：字段各自 round 到 2 位）
    assert result.allocated_value + result.leftover_cash == pytest.approx(
        total, abs=0.02
    )


class TestBudgetConservation:
    @pytest.mark.parametrize(
        "method", [AllocationMethod.GREEDY, AllocationMethod.LP]
    )
    def test_budget_conserved(self, method: AllocationMethod) -> None:
        # Arrange
        weights = {"AAPL": 0.5, "MSFT": 0.3, "GOOGL": 0.2}
        prices = {"AAPL": 190.0, "MSFT": 410.0, "GOOGL": 140.0}
        total = 100_000.0

        # Act
        result = allocate(weights, prices, total, method=method)

        # Assert
        _assert_budget_conserved(result, total)
        # 逐股复核：Σ shares*price == allocated_value
        recomputed = sum(
            result.shares[s] * prices[s] for s in result.shares
        )
        assert recomputed == pytest.approx(result.allocated_value, abs=0.01)

    @pytest.mark.parametrize(
        "method", [AllocationMethod.GREEDY, AllocationMethod.LP]
    )
    def test_leftover_non_negative_small_budget(
        self, method: AllocationMethod
    ) -> None:
        # Arrange: 预算仅够买几股高价标的
        weights = {"BRKA": 0.6, "AAPL": 0.4}
        prices = {"BRKA": 600_000.0, "AAPL": 190.0}
        total = 5_000.0

        # Act
        result = allocate(weights, prices, total, method=method)

        # Assert: 买不起 BRKA，leftover 仍非负、不超支
        _assert_budget_conserved(result, total)

    def test_greedy_accepts_series_prices(self) -> None:
        # Arrange: 价格以 pd.Series 传入
        weights = {"AAPL": 0.5, "MSFT": 0.5}
        prices = pd.Series({"AAPL": 190.0, "MSFT": 410.0})
        total = 50_000.0

        # Act
        result = greedy_allocation(weights, prices, total)

        # Assert
        _assert_budget_conserved(result, total)


class TestRenormalizationAndSkipping:
    def test_skipped_symbol_reweights_remaining(self) -> None:
        # Arrange: GOOGL 无价格 → 应被跳过，其余权重重新归一化
        weights = {"AAPL": 0.5, "MSFT": 0.3, "GOOGL": 0.2}
        prices = {"AAPL": 190.0, "MSFT": 410.0}  # 缺 GOOGL
        total = 100_000.0

        # Act
        result = greedy_allocation(weights, prices, total)

        # Assert: GOOGL 被跳过
        assert "GOOGL" in result.skipped
        assert "GOOGL" not in result.shares
        # 剩余标的实际权重之和 ≈ 1（在已配置价值上归一）
        assert sum(result.allocation_weights.values()) == pytest.approx(
            1.0, abs=0.05
        )

    def test_zero_price_symbol_skipped(self) -> None:
        # Arrange: 价格 <= 0 视为无效
        weights = {"AAPL": 0.5, "DEAD": 0.5}
        prices = {"AAPL": 190.0, "DEAD": 0.0}
        total = 20_000.0

        # Act
        result = greedy_allocation(weights, prices, total)

        # Assert
        assert "DEAD" in result.skipped
        assert "DEAD" not in result.shares

    def test_negligible_weight_dropped_not_skipped(self) -> None:
        # Arrange: 极小权重被丢弃但不计入 skipped
        weights = {"AAPL": 0.9999, "DUST": 0.00001}
        prices = {"AAPL": 190.0, "DUST": 10.0}
        total = 50_000.0

        # Act
        result = greedy_allocation(weights, prices, total)

        # Assert
        assert "DUST" not in result.shares
        assert "DUST" not in result.skipped


class TestValidation:
    def test_negative_weight_raises(self) -> None:
        weights = {"AAPL": 0.7, "MSFT": -0.3}
        prices = {"AAPL": 190.0, "MSFT": 410.0}
        with pytest.raises(ValueError, match="负权重"):
            greedy_allocation(weights, prices, 100_000.0)

    def test_nan_weight_raises(self) -> None:
        weights = {"AAPL": 0.5, "MSFT": float("nan")}
        prices = {"AAPL": 190.0, "MSFT": 410.0}
        with pytest.raises(ValueError):
            greedy_allocation(weights, prices, 100_000.0)

    def test_empty_weights_raises(self) -> None:
        with pytest.raises(ValueError):
            greedy_allocation({}, {}, 100_000.0)

    def test_non_positive_budget_raises(self) -> None:
        weights = {"AAPL": 1.0}
        prices = {"AAPL": 190.0}
        with pytest.raises(ValueError):
            greedy_allocation(weights, prices, 0.0)

    def test_all_prices_missing_raises(self) -> None:
        weights = {"AAPL": 0.5, "MSFT": 0.5}
        prices: dict[str, float] = {}
        with pytest.raises(ValueError):
            greedy_allocation(weights, prices, 100_000.0)

    def test_unknown_method_raises(self) -> None:
        weights = {"AAPL": 1.0}
        prices = {"AAPL": 190.0}
        with pytest.raises(ValueError):
            allocate(weights, prices, 100_000.0, method="bogus")


class TestLpFallback:
    def test_lp_matches_budget_like_greedy(self) -> None:
        # Arrange
        weights = {"A": 0.4, "B": 0.35, "C": 0.25}
        prices = {"A": 55.0, "B": 120.0, "C": 33.0}
        total = 75_000.0

        # Act
        lp = lp_allocation(weights, prices, total)

        # Assert: LP 也守恒预算
        _assert_budget_conserved(lp, total)
        assert lp.method == AllocationMethod.LP.value
