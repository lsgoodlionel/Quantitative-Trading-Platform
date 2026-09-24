"""
V3 Wave A-b（G2）：组合再平衡计划纯函数测试。

`plan_rebalance()` 是回测（`PortfolioContext.target_weight`）与实盘 API
共用的**唯一**一份「目标权重 → 股数增量」算法，这里锁死它的行为边界。
"""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest

from app.engine.portfolio.rebalance import (
    RebalanceLeg,
    plan_rebalance,
    validate_target_weights,
)


def _by_symbol(legs: list[RebalanceLeg]) -> dict[str, RebalanceLeg]:
    return {leg.symbol: leg for leg in legs}


# ── 1. 增量 diff：已有持仓要被扣掉 ────────────────────────────────


class TestIncrementalDiff:
    def test_existing_position_is_subtracted(self) -> None:
        # Arrange：净值 15000，权重 1.0，价格 100 → 目标 150 股；已有 100 股
        # Act
        legs = plan_rebalance(
            target_weights={"AAA": 1.0},
            current_qty={"AAA": 100},
            prices={"AAA": 100.0},
            portfolio_value=15_000.0,
        )

        # Assert
        assert len(legs) == 1
        assert legs[0].target_qty == 150
        assert legs[0].delta_qty == 50          # 不是 150
        assert legs[0].reason == "increase"
        assert legs[0].side == "BUY"
        assert legs[0].delta_value == pytest.approx(5_000.0)

    def test_open_from_flat(self) -> None:
        legs = plan_rebalance(
            target_weights={"AAA": 1.0},
            current_qty={},
            prices={"AAA": 50.0},
            portfolio_value=10_000.0,
        )

        assert legs[0].current_qty == 0
        assert legs[0].delta_qty == 200
        assert legs[0].reason == "open"

    def test_reducing_weight_produces_sell(self) -> None:
        legs = plan_rebalance(
            target_weights={"AAA": 0.5},
            current_qty={"AAA": 200},
            prices={"AAA": 100.0},
            portfolio_value=10_000.0,
        )

        assert legs[0].delta_qty == -150
        assert legs[0].side == "SELL"
        assert legs[0].reason == "decrease"

    def test_zero_delta_emits_no_leg(self) -> None:
        legs = plan_rebalance(
            target_weights={"AAA": 1.0},
            current_qty={"AAA": 100},
            prices={"AAA": 100.0},
            portfolio_value=10_000.0,
        )

        assert legs == []

    def test_untargeted_holdings_are_left_alone(self) -> None:
        """不在 target_weights 里的持仓不会被自动清空（与 K-c 语义一致）。"""
        legs = plan_rebalance(
            target_weights={"AAA": 1.0},
            current_qty={"AAA": 0, "BBB": 300},
            prices={"AAA": 100.0, "BBB": 20.0},
            portfolio_value=10_000.0,
        )

        assert _by_symbol(legs).keys() == {"AAA"}


# ── 2. 目标权重 0 → 清仓 ─────────────────────────────────────────


class TestCloseOut:
    def test_zero_weight_sells_everything(self) -> None:
        legs = plan_rebalance(
            target_weights={"AAA": 0.0},
            current_qty={"AAA": 137},
            prices={"AAA": 12.5},
            portfolio_value=10_000.0,
        )

        assert len(legs) == 1
        assert legs[0].target_qty == 0
        assert legs[0].delta_qty == -137
        assert legs[0].reason == "close"
        assert legs[0].delta_value == pytest.approx(-137 * 12.5)

    def test_zero_weight_on_flat_symbol_is_noop(self) -> None:
        legs = plan_rebalance(
            target_weights={"AAA": 0.0},
            current_qty={},
            prices={"AAA": 12.5},
            portfolio_value=10_000.0,
        )

        assert legs == []


# ── 3. min_trade_value 过滤碎单 ──────────────────────────────────


class TestMinTradeValue:
    def test_small_leg_is_filtered_out(self) -> None:
        # 目标 101 股 vs 已有 100 股 → 1 股 × 100 元 = 100 元 < 500 门槛
        legs = plan_rebalance(
            target_weights={"AAA": 1.0},
            current_qty={"AAA": 100},
            prices={"AAA": 100.0},
            portfolio_value=10_100.0,
            min_trade_value=500.0,
        )

        assert legs == []

    def test_large_leg_survives_the_filter(self) -> None:
        legs = plan_rebalance(
            target_weights={"AAA": 1.0},
            current_qty={"AAA": 100},
            prices={"AAA": 100.0},
            portfolio_value=11_000.0,
            min_trade_value=500.0,
        )

        assert legs[0].delta_qty == 10          # 1000 元 ≥ 500

    def test_negative_min_trade_value_rejected(self) -> None:
        with pytest.raises(ValueError, match="min_trade_value"):
            plan_rebalance(
                target_weights={"AAA": 1.0},
                current_qty={},
                prices={"AAA": 100.0},
                portfolio_value=10_000.0,
                min_trade_value=-1.0,
            )


# ── 4. lot_size 整手取整（港股 / A 股）────────────────────────────


class TestLotSize:
    def test_target_truncates_to_lot(self) -> None:
        # 目标 258 股，整手 100 → 200 股（向零取整 = 保守暴露）
        legs = plan_rebalance(
            target_weights={"AAA": 1.0},
            current_qty={},
            prices={"AAA": 10.0},
            portfolio_value=2_580.0,
            lot_size=100,
        )

        assert legs[0].target_qty == 200
        assert legs[0].delta_qty == 200

    def test_lot_size_one_is_share_level(self) -> None:
        legs = plan_rebalance(
            target_weights={"AAA": 1.0},
            current_qty={},
            prices={"AAA": 10.0},
            portfolio_value=2_580.0,
            lot_size=1,
        )

        assert legs[0].target_qty == 258

    def test_invalid_lot_size_rejected(self) -> None:
        with pytest.raises(ValueError, match="lot_size"):
            plan_rebalance(
                target_weights={"AAA": 1.0},
                current_qty={},
                prices={"AAA": 10.0},
                portfolio_value=1_000.0,
                lot_size=0,
            )


# ── 5. 权重校验：报错而非静默归一化 ──────────────────────────────


class TestValidateTargetWeights:
    def test_sum_not_one_raises(self) -> None:
        with pytest.raises(ValueError, match="权重和"):
            validate_target_weights({"AAA": 0.5, "BBB": 0.25})

    def test_sum_one_passes(self) -> None:
        validate_target_weights({"AAA": 0.5, "BBB": 0.5})   # 不抛错即通过

    def test_float_noise_within_tolerance_passes(self) -> None:
        validate_target_weights({"AAA": 1 / 3, "BBB": 1 / 3, "CCC": 1 / 3})

    def test_empty_weights_raises(self) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            validate_target_weights({})

    def test_negative_weight_rejected_without_short(self) -> None:
        with pytest.raises(ValueError, match="做空"):
            validate_target_weights({"AAA": 1.5, "BBB": -0.5})

    def test_negative_weight_allowed_with_short(self) -> None:
        validate_target_weights({"AAA": 1.5, "BBB": -0.5}, allow_short=True)

    def test_nan_weight_rejected(self) -> None:
        with pytest.raises(ValueError, match="非法权重"):
            validate_target_weights({"AAA": math.nan})

    def test_inf_weight_rejected(self) -> None:
        with pytest.raises(ValueError, match="非法权重"):
            validate_target_weights({"AAA": math.inf})


# ── 6. 价格缺失 / 非法：跳过而不是算出垃圾单 ──────────────────────


class TestMissingPrice:
    def test_symbol_without_price_is_skipped(self) -> None:
        legs = plan_rebalance(
            target_weights={"AAA": 0.5, "BBB": 0.5},
            current_qty={},
            prices={"AAA": 100.0},
            portfolio_value=10_000.0,
        )

        assert _by_symbol(legs).keys() == {"AAA"}

    def test_non_positive_price_is_skipped(self) -> None:
        legs = plan_rebalance(
            target_weights={"AAA": 1.0},
            current_qty={},
            prices={"AAA": 0.0},
            portfolio_value=10_000.0,
        )

        assert legs == []


# ── 7. 排序：先卖后买，让释放的现金能被同一批买单用上 ─────────────


class TestOrdering:
    def test_sells_come_before_buys(self) -> None:
        legs = plan_rebalance(
            target_weights={"AAA": 0.5, "BBB": 0.0, "CCC": 0.5},
            current_qty={"BBB": 100, "CCC": 10},
            prices={"AAA": 100.0, "BBB": 50.0, "CCC": 100.0},
            portfolio_value=10_000.0,
        )

        sides = [leg.side for leg in legs]
        assert sides == ["SELL", "BUY", "BUY"]
        assert [leg.symbol for leg in legs] == ["BBB", "AAA", "CCC"]


# ── 8. 做空腿：符号正确，取整仍然向零 ────────────────────────────


class TestShortLeg:
    def test_negative_target_truncates_toward_zero(self) -> None:
        legs = plan_rebalance(
            target_weights={"AAA": -1.0},
            current_qty={},
            prices={"AAA": 10.0},
            portfolio_value=2_580.0,
            lot_size=100,
        )

        assert legs[0].target_qty == -200
        assert legs[0].delta_qty == -200
        assert legs[0].reason == "open"


# ── 9. 回测路径确实复用本模块（只有一份 diff 逻辑）────────────────


class TestBacktestUsesSamePlanner:
    def test_target_weight_delegates_to_plan_rebalance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`PortfolioContext.target_weight()` 必须调用 plan_rebalance，而非自带算法。"""
        from app.strategy import context as context_module

        calls: list[dict] = []
        original = context_module.plan_rebalance

        def _spy(**kwargs):
            calls.append(kwargs)
            return original(**kwargs)

        monkeypatch.setattr(context_module, "plan_rebalance", _spy)

        from app.engine.backtest.portfolio_engine import PortfolioBacktestEngine
        from tests.test_portfolio_engine import TargetWeightOnce, _config, _make_bars

        bars = {"AAA": _make_bars("AAA", n=8, base_price=100.0)}
        strategy = TargetWeightOnce({"AAA": 0.5}, at_index=0)
        PortfolioBacktestEngine(_config()).run(strategy, bars)

        assert calls, "target_weight 没有走 plan_rebalance —— diff 逻辑又分叉了"
        assert calls[0]["target_weights"] == {"AAA": 0.5}
        assert strategy.orders[0].qty == 500       # 100_000 × 0.5 / 100


# ── 10. 不可变性 ─────────────────────────────────────────────────


class TestImmutability:
    def test_leg_is_frozen(self) -> None:
        legs = plan_rebalance(
            target_weights={"AAA": 1.0},
            current_qty={},
            prices={"AAA": 100.0},
            portfolio_value=10_000.0,
        )

        with pytest.raises(FrozenInstanceError):
            legs[0].delta_qty = 1  # type: ignore[misc]

    def test_inputs_are_not_mutated(self) -> None:
        weights = {"AAA": 1.0}
        qty = {"AAA": 10}
        prices = {"AAA": 100.0}

        plan_rebalance(
            target_weights=weights,
            current_qty=qty,
            prices=prices,
            portfolio_value=10_000.0,
        )

        assert weights == {"AAA": 1.0}
        assert qty == {"AAA": 10}
        assert prices == {"AAA": 100.0}
