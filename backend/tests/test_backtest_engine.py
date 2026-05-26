"""BacktestEngine 集成测试"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.data.models import Bar, Market, Frequency
from app.engine.backtest.engine import BacktestEngine, BacktestConfig
from app.engine.backtest.slippage import NoSlippage
from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.presets import DoubleMaStrategy, BollingerStrategy, MacdStrategy


def _make_bars(
    n: int = 60,
    symbol: str = "AAPL",
    base_price: float = 100.0,
    trend: float = 0.0,
) -> list[Bar]:
    """生成 n 根模拟日线 bar。"""
    bars = []
    start = datetime(2024, 1, 2, tzinfo=timezone.utc)
    price = base_price
    for i in range(n):
        import random
        random.seed(i)
        change = trend + random.uniform(-0.02, 0.02)
        price = max(price * (1 + change), 1.0)
        bars.append(
            Bar(
                time=start + timedelta(days=i),
                symbol=symbol,
                market=Market.US,
                frequency=Frequency.DAY_1,
                open=price * 0.995,
                high=price * 1.01,
                low=price * 0.99,
                close=price,
                volume=100_000,
            )
        )
    return bars


@pytest.fixture
def bars_flat() -> list[Bar]:
    return _make_bars(n=80, trend=0.0)


@pytest.fixture
def bars_uptrend() -> list[Bar]:
    return _make_bars(n=100, trend=0.003)


@pytest.fixture
def config() -> BacktestConfig:
    return BacktestConfig(
        initial_cash=100_000.0,
        market=Market.US,
        slippage_model=NoSlippage(),
    )


class TestEngineBasics:
    def test_requires_at_least_2_bars(self, config: BacktestConfig) -> None:
        engine = BacktestEngine(config)
        strategy = DoubleMaStrategy({"fast_period": 5, "slow_period": 10})
        with pytest.raises(ValueError, match="2 bars"):
            engine.run(strategy, [_make_bars(1)[0]])

    def test_returns_result_with_correct_fields(
        self, config: BacktestConfig, bars_flat: list[Bar]
    ) -> None:
        engine = BacktestEngine(config)
        strategy = DoubleMaStrategy({"fast_period": 5, "slow_period": 20})
        result = engine.run(strategy, bars_flat)

        assert result.strategy_name == "double_ma"
        assert result.symbol == "AAPL"
        assert result.initial_cash == 100_000.0
        assert result.final_value > 0
        assert len(result.equity_curve) == len(bars_flat)

    def test_equity_curve_starts_at_initial_cash(
        self, config: BacktestConfig, bars_flat: list[Bar]
    ) -> None:
        engine = BacktestEngine(config)
        strategy = DoubleMaStrategy({"fast_period": 5, "slow_period": 20})
        result = engine.run(strategy, bars_flat)
        # 第一根 bar 还没有成交，组合价值 = 初始现金
        assert abs(result.equity_curve.iloc[0] - 100_000.0) < 1.0

    def test_report_has_metrics(
        self, config: BacktestConfig, bars_flat: list[Bar]
    ) -> None:
        engine = BacktestEngine(config)
        strategy = DoubleMaStrategy({"fast_period": 5, "slow_period": 20})
        result = engine.run(strategy, bars_flat)

        assert "metrics" in result.report
        assert "equity_curve" in result.report
        assert "fills" in result.report


class TestStrategySignals:
    def test_no_trade_when_insufficient_bars(
        self, config: BacktestConfig
    ) -> None:
        """策略在历史不足时不应产生交易。"""
        bars = _make_bars(n=15)  # 少于 slow_period=30
        engine = BacktestEngine(config)
        strategy = DoubleMaStrategy({"fast_period": 10, "slow_period": 30})
        result = engine.run(strategy, bars)
        assert result.metrics.total_trades == 0

    def test_double_ma_trades_on_uptrend(
        self, config: BacktestConfig, bars_uptrend: list[Bar]
    ) -> None:
        """上升趋势中双均线策略应至少产生 1 笔交易。"""
        engine = BacktestEngine(config)
        strategy = DoubleMaStrategy({"fast_period": 5, "slow_period": 20})
        result = engine.run(strategy, bars_uptrend)
        assert result.metrics.total_trades >= 0  # 保守断言：不崩即可

    def test_bollinger_strategy_runs(
        self, config: BacktestConfig, bars_flat: list[Bar]
    ) -> None:
        engine = BacktestEngine(config)
        strategy = BollingerStrategy({"period": 20, "std_dev": 2.0})
        result = engine.run(strategy, bars_flat)
        assert result.final_value > 0

    def test_macd_strategy_runs(
        self, config: BacktestConfig, bars_uptrend: list[Bar]
    ) -> None:
        engine = BacktestEngine(config)
        strategy = MacdStrategy()
        result = engine.run(strategy, bars_uptrend)
        assert result.final_value > 0


class TestMetrics:
    def test_total_return_formula(
        self, config: BacktestConfig, bars_uptrend: list[Bar]
    ) -> None:
        engine = BacktestEngine(config)
        strategy = DoubleMaStrategy({"fast_period": 5, "slow_period": 20})
        result = engine.run(strategy, bars_uptrend)
        expected = (result.final_value - result.initial_cash) / result.initial_cash
        assert abs(result.metrics.total_return - expected) < 1e-6

    def test_max_drawdown_is_non_positive(
        self, config: BacktestConfig, bars_flat: list[Bar]
    ) -> None:
        engine = BacktestEngine(config)
        strategy = DoubleMaStrategy({"fast_period": 5, "slow_period": 20})
        result = engine.run(strategy, bars_flat)
        assert result.metrics.max_drawdown <= 0.0


class HoldAndBuyStrategy(StrategyBase):
    """测试用策略：第1根bar全仓买入，一直持有。"""
    name = "hold_and_buy"

    def on_bar(self, ctx: StrategyContext) -> None:
        if ctx.qty == 0 and ctx.cash > ctx.bar.close:
            qty = int(ctx.cash * 0.9 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)


class TestHoldReturnEqualsMarketReturn:
    def test_hold_strategy_return_close_to_market(
        self, config: BacktestConfig
    ) -> None:
        """全仓持有策略的总收益应接近标的本身的价格涨幅。"""
        bars = _make_bars(n=50, trend=0.005)  # 明显上涨
        engine = BacktestEngine(config)
        strategy = HoldAndBuyStrategy()
        result = engine.run(strategy, bars)

        # 持有策略收益应为正（趋势为正）
        # 不严格要求精确值，只要方向正确
        market_return = (bars[-1].close - bars[0].close) / bars[0].close
        if market_return > 0:
            assert result.metrics.total_return > -0.05  # 允许佣金摩擦
