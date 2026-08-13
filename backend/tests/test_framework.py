"""Alpha → Insight → PortfolioTarget → Execution 三段式框架测试（Wave K-d / K5）

对应契约 docs/contracts/waveKd-strategy-hooks-insight.md §二 与 §三.3/§三.4。
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.broker import OrderSide
from app.engine.backtest.commission import CommissionResult
from app.engine.backtest.engine import BacktestConfig, BacktestEngine
from app.engine.backtest.portfolio_broker import PortfolioBroker
from app.engine.backtest.portfolio_engine import (
    PortfolioBacktestConfig,
    PortfolioBacktestEngine,
)
from app.engine.backtest.slippage import NoSlippage
from app.engine.framework import (
    AlphaModel,
    EqualWeightingPCM,
    ExecutionModel,
    FormulaFactorAlphaModel,
    FrameworkStrategy,
    ImmediateExecutionModel,
    Insight,
    InsightDirection,
    InsightWeightingPCM,
    LegacyStrategyAlphaAdapter,
    NullRiskModel,
    OptimizerPCM,
    PortfolioConstructionModel,
    PortfolioTarget,
    RiskManagementModel,
    group_insights,
)
from app.engine.portfolio.optimizer import OptimizeMethod
from app.strategy.context import PortfolioContext
from app.strategy.presets.macd import MacdStrategy

START = datetime(2024, 1, 2, tzinfo=UTC)
DEFAULT_PERIOD = timedelta(days=5)


class _ZeroCommission:
    def calculate(self, price: float, qty: int, side: str) -> CommissionResult:
        return CommissionResult(commission=0.0, fees=0.0, total=0.0)


def _bar(symbol: str, price: float, ts: datetime) -> Bar:
    return Bar(
        time=ts,
        symbol=symbol,
        market=Market.US,
        frequency=Frequency.DAY_1,
        open=price,
        high=price * 1.001,
        low=price * 0.999,
        close=price,
        volume=1_000_000,
    )


def _series_bars(symbol: str, closes: list[float]) -> list[Bar]:
    return [_bar(symbol, p, START + timedelta(days=i)) for i, p in enumerate(closes)]


def _frame(closes: list[float]) -> pd.DataFrame:
    index = pd.DatetimeIndex([START + timedelta(days=i) for i in range(len(closes))])
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.001 for c in closes],
            "low": [c * 0.999 for c in closes],
            "close": closes,
            "volume": [1_000_000.0] * len(closes),
        },
        index=index,
    )


def _context(
    prices: dict[str, float],
    *,
    cash: float = 100_000.0,
    positions: dict[str, int] | None = None,
    histories: dict[str, pd.DataFrame] | None = None,
    priced_symbols: set[str] | None = None,
) -> PortfolioContext:
    """构造一个可直接喂给 PCM / ExecutionModel 的组合上下文。

    priced_symbols 缺省为 prices 的全部键；显式传入可模拟「某标的本时点无行情」。
    """
    symbols = sorted(prices)
    broker = PortfolioBroker(
        initial_cash=cash,
        market=Market.US,
        commission_model=_ZeroCommission(),
        slippage_model=NoSlippage(),
    )
    tradable = symbols if priced_symbols is None else sorted(priced_symbols)
    bars = {s: _bar(s, prices[s], START) for s in tradable}
    broker.process_bars(START, bars)      # 无挂单 → 只是把最后已知价灌进券商
    for symbol, qty in (positions or {}).items():
        broker.positions.buy(symbol, qty, prices[symbol])
    return PortfolioContext(
        time=START,
        bars=bars,
        symbols=symbols,
        broker=broker,
        histories=histories or {s: _frame([prices[s]] * 3) for s in symbols},
        market=Market.US,
    )


def _insight(symbol: str, direction: InsightDirection, **kwargs) -> Insight:
    return Insight(
        symbol=symbol,
        direction=direction,
        period=DEFAULT_PERIOD,
        generated_at=START,
        **kwargs,
    )


# ── 1. Insight 数据结构 ───────────────────────────────────────


def test_insight_has_stable_id_and_expiry():
    insight = _insight("AAPL", InsightDirection.UP)
    assert len(insight.insight_id) > 0
    assert insight.group_id is None
    assert insight.close_time == START + DEFAULT_PERIOD
    assert insight.is_expired_at(START + timedelta(days=4)) is False
    assert insight.is_expired_at(START + timedelta(days=6)) is True


def test_insight_ids_are_unique():
    a = _insight("AAPL", InsightDirection.UP)
    b = _insight("AAPL", InsightDirection.UP)
    assert a.insight_id != b.insight_id


def test_group_insights_assigns_shared_group_id_without_mutating_inputs():
    long_leg = _insight("AAPL", InsightDirection.UP)
    short_leg = _insight("MSFT", InsightDirection.DOWN)

    grouped = group_insights(long_leg, short_leg)

    assert len({i.group_id for i in grouped}) == 1
    assert grouped[0].group_id is not None
    assert long_leg.group_id is None       # frozen：原实例不被改组
    assert short_leg.group_id is None
    assert [i.insight_id for i in grouped] == [long_leg.insight_id, short_leg.insight_id]


def test_group_insights_rejects_already_grouped():
    grouped = group_insights(_insight("AAPL", InsightDirection.UP))
    with pytest.raises(ValueError, match="已属于"):
        group_insights(grouped[0], _insight("MSFT", InsightDirection.DOWN))


# ── 2. 组合构建模型 ───────────────────────────────────────────


def test_equal_weighting_gives_each_up_insight_one_over_n():
    ctx = _context({"AAPL": 100.0, "MSFT": 200.0, "NVDA": 50.0})
    insights = [
        _insight("AAPL", InsightDirection.UP),
        _insight("MSFT", InsightDirection.UP),
        _insight("NVDA", InsightDirection.UP),
    ]

    percents = EqualWeightingPCM().determine_target_percent(ctx, insights)

    assert percents == pytest.approx({"AAPL": 1 / 3, "MSFT": 1 / 3, "NVDA": 1 / 3})


def test_equal_weighting_flat_insight_targets_zero():
    ctx = _context({"AAPL": 100.0, "MSFT": 200.0})
    insights = [
        _insight("AAPL", InsightDirection.UP),
        _insight("MSFT", InsightDirection.FLAT),
    ]

    targets = EqualWeightingPCM().create_targets(ctx, insights)
    by_symbol = {t.symbol: t.quantity for t in targets}

    assert by_symbol["MSFT"] == 0
    assert by_symbol["AAPL"] == int(100_000.0 / 100.0)   # 唯一非 FLAT → 权重 1


def test_equal_weighting_liquidates_symbols_without_insight():
    ctx = _context({"AAPL": 100.0, "MSFT": 200.0}, positions={"MSFT": 10})
    targets = EqualWeightingPCM().create_targets(
        ctx, [_insight("AAPL", InsightDirection.UP)]
    )
    assert {t.symbol: t.quantity for t in targets}["MSFT"] == 0


def test_insight_weighting_uses_insight_weight():
    ctx = _context({"AAPL": 100.0, "MSFT": 100.0})
    insights = [
        _insight("AAPL", InsightDirection.UP, weight=0.6),
        _insight("MSFT", InsightDirection.UP, weight=0.2),
    ]

    percents = InsightWeightingPCM().determine_target_percent(ctx, insights)

    assert percents == pytest.approx({"AAPL": 0.6, "MSFT": 0.2})


def test_insight_weighting_scales_down_when_sum_exceeds_one():
    ctx = _context({"AAPL": 100.0, "MSFT": 100.0})
    insights = [
        _insight("AAPL", InsightDirection.UP, weight=0.9),
        _insight("MSFT", InsightDirection.UP, weight=0.9),
    ]

    percents = InsightWeightingPCM().determine_target_percent(ctx, insights)

    assert sum(percents.values()) == pytest.approx(1.0)
    assert percents["AAPL"] == pytest.approx(0.5)


def test_grouped_insights_are_dropped_together_when_a_leg_is_untradable():
    """配对策略的一条腿没有行情时，另一条腿也必须被丢弃 —— 否则留下裸露敞口。"""
    ctx = _context(
        {"AAPL": 100.0, "MSFT": 200.0},
        priced_symbols={"AAPL"},          # MSFT 本时点停牌且从未有过价格
    )
    long_leg, short_leg = group_insights(
        _insight("AAPL", InsightDirection.UP),
        _insight("MSFT", InsightDirection.DOWN),
    )

    percents = EqualWeightingPCM().determine_target_percent(ctx, [long_leg, short_leg])

    assert percents == {}


def test_optimizer_pcm_hrp_weights_sum_to_one():
    symbols = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN"]
    histories = {
        s: _frame([100.0 + 8 * math.sin((i + k * 7) / 9.0) + i * 0.05 for i in range(120)])
        for k, s in enumerate(symbols)
    }
    prices = {s: float(histories[s]["close"].iloc[-1]) for s in symbols}
    ctx = _context(prices, histories=histories)
    insights = [_insight(s, InsightDirection.UP) for s in symbols]

    pcm = OptimizerPCM(method=OptimizeMethod.HRP)
    percents = pcm.determine_target_percent(ctx, insights)

    assert set(percents) == set(symbols)
    assert sum(percents.values()) == pytest.approx(1.0, abs=1e-6)
    assert all(w >= 0 for w in percents.values())

    targets = pcm.create_targets(ctx, insights)
    notional = sum(t.quantity * prices[t.symbol] for t in targets)
    assert notional == pytest.approx(ctx.portfolio_value, rel=0.01)


def test_optimizer_pcm_falls_back_to_equal_weight_on_short_history():
    ctx = _context({"AAPL": 100.0, "MSFT": 100.0})   # 只有 3 根历史
    insights = [
        _insight("AAPL", InsightDirection.UP),
        _insight("MSFT", InsightDirection.UP),
    ]
    percents = OptimizerPCM(method=OptimizeMethod.HRP).determine_target_percent(
        ctx, insights
    )
    assert percents == pytest.approx({"AAPL": 0.5, "MSFT": 0.5})


def test_should_rebalance_honours_period():
    pcm = EqualWeightingPCM(rebalance_period=timedelta(days=3))
    assert pcm.should_rebalance(START) is True
    assert pcm.should_rebalance(START + timedelta(days=1)) is False
    assert pcm.should_rebalance(START + timedelta(days=3)) is True


# ── 3. 风控与执行 ─────────────────────────────────────────────


def test_null_risk_model_is_passthrough():
    ctx = _context({"AAPL": 100.0})
    targets = [PortfolioTarget(symbol="AAPL", quantity=10)]
    assert NullRiskModel().manage_risk(ctx, targets) == targets


def test_immediate_execution_emits_incremental_diff_orders():
    ctx = _context({"AAPL": 100.0}, positions={"AAPL": 100})
    orders = ImmediateExecutionModel().execute(
        ctx, [PortfolioTarget(symbol="AAPL", quantity=150)]
    )

    assert len(orders) == 1
    assert orders[0].side is OrderSide.BUY
    assert orders[0].qty == 50            # 增量，而不是 150


def test_immediate_execution_sells_down_to_target():
    ctx = _context({"AAPL": 100.0}, positions={"AAPL": 100})
    orders = ImmediateExecutionModel().execute(
        ctx, [PortfolioTarget(symbol="AAPL", quantity=40)]
    )
    assert orders[0].side is OrderSide.SELL
    assert orders[0].qty == 60


def test_immediate_execution_skips_targets_already_met():
    ctx = _context({"AAPL": 100.0}, positions={"AAPL": 100})
    orders = ImmediateExecutionModel().execute(
        ctx, [PortfolioTarget(symbol="AAPL", quantity=100)]
    )
    assert orders == []


def test_immediate_execution_drops_whole_group_when_a_leg_is_untradable():
    ctx = _context({"AAPL": 100.0, "MSFT": 200.0}, priced_symbols={"AAPL"})
    orders = ImmediateExecutionModel().execute(
        ctx,
        [
            PortfolioTarget(symbol="AAPL", quantity=10, group_id="pair-1"),
            PortfolioTarget(symbol="MSFT", quantity=-10, group_id="pair-1"),
        ],
    )
    assert orders == []


# ── 4. LegacyStrategyAlphaAdapter ─────────────────────────────


def _oscillating(n: int = 220) -> list[float]:
    return [round(100.0 * (1 + 0.12 * math.sin(i / 11.0)) + i * 0.03, 4) for i in range(n)]


class _SpyMacd(MacdStrategy):
    """记录原策略在哪些时点下了什么方向的单（作为一一对应的比对基准）。"""

    def on_start(self, ctx) -> None:
        self.signals: list[tuple[datetime, str]] = []

    def on_bar(self, ctx) -> None:
        before = len(ctx.broker._pending)
        super().on_bar(ctx)
        for order in ctx.broker._pending[before:]:
            self.signals.append((ctx.bar.time, order.side.value))


def _portfolio_config(**kwargs) -> PortfolioBacktestConfig:
    defaults = {
        "initial_cash": 100_000.0,
        "market": Market.US,
        "slippage_model": NoSlippage(),
        "commission_model": _ZeroCommission(),
    }
    defaults.update(kwargs)
    return PortfolioBacktestConfig(**defaults)


def test_legacy_adapter_insights_match_preset_signals_one_to_one():
    bars = _series_bars("AAPL", _oscillating())

    spy = _SpyMacd()
    legacy = BacktestEngine(
        BacktestConfig(
            initial_cash=100_000.0,
            market=Market.US,
            slippage_model=NoSlippage(),
            commission_model=_ZeroCommission(),
        )
    ).run(spy, bars)

    framework = FrameworkStrategy(
        alpha=LegacyStrategyAlphaAdapter(MacdStrategy),
        portfolio_construction=EqualWeightingPCM(),
        execution=ImmediateExecutionModel(),
    )
    result = PortfolioBacktestEngine(_portfolio_config()).run(framework, {"AAPL": bars})

    expected_direction = {"BUY": InsightDirection.UP, "SELL": InsightDirection.FLAT}
    assert len(spy.signals) >= 4                     # 保证样本不是空的
    assert [(i.generated_at, i.direction) for i in framework.emitted] == [
        (ts, expected_direction[side]) for ts, side in spy.signals
    ]
    # 成交时点与方向也必须逐笔对齐（数量口径不同：95% 现金 vs 100% 净值）
    assert [(f["filled_at"], f["side"]) for f in result.fills] == [
        (f["filled_at"], f["side"]) for f in legacy.fills
    ]


def test_legacy_adapter_rejects_shared_instance_across_symbols():
    adapter = LegacyStrategyAlphaAdapter(MacdStrategy())
    ctx = _context({"AAPL": 100.0, "MSFT": 100.0})
    with pytest.raises(ValueError, match="多标的"):
        adapter.update(ctx)


# ── 5. FrameworkStrategy 编排 ─────────────────────────────────


class _ConstantAlpha(AlphaModel):
    name = "constant"

    def __init__(self, direction: InsightDirection = InsightDirection.UP) -> None:
        self._direction = direction

    def update(self, ctx: PortfolioContext) -> list[Insight]:
        return [
            Insight(
                symbol=s,
                direction=self._direction,
                period=timedelta(days=30),
                generated_at=ctx.time,
                source=self.name,
            )
            for s in ctx.bars
        ]


def test_framework_strategy_runs_alpha_pcm_execution_pipeline():
    bars = {
        "AAPL": _series_bars("AAPL", [100.0 + i * 0.5 for i in range(40)]),
        "MSFT": _series_bars("MSFT", [200.0 - i * 0.3 for i in range(40)]),
    }
    strategy = FrameworkStrategy(
        alpha=_ConstantAlpha(),
        portfolio_construction=EqualWeightingPCM(),
        risk=NullRiskModel(),
        execution=ImmediateExecutionModel(),
    )
    result = PortfolioBacktestEngine(_portfolio_config()).run(strategy, bars)

    assert result.fills, "等权常量 alpha 必须产生成交"
    assert set(result.symbols) == {"AAPL", "MSFT"}
    assert result.report["metrics"]["total_return_pct"] is not None


def test_framework_strategy_requires_alpha_and_pcm():
    with pytest.raises(TypeError):
        FrameworkStrategy(alpha=None, portfolio_construction=EqualWeightingPCM())


def test_abstract_models_cannot_be_instantiated():
    for cls in (AlphaModel, PortfolioConstructionModel, RiskManagementModel, ExecutionModel):
        with pytest.raises(TypeError):
            cls()


# ── 6. 端到端：因子 Alpha + HRP 优化 + 立即执行 ────────────────


def _factor_universe(n: int = 150) -> dict[str, list[Bar]]:
    symbols = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN"]
    return {
        s: _series_bars(
            s,
            [
                round(100.0 + 10 * math.sin((i + k * 5) / 13.0) + i * (0.02 + 0.01 * k), 4)
                for i in range(n)
            ],
        )
        for k, s in enumerate(symbols)
    }


def test_end_to_end_formula_factor_hrp_portfolio_backtest():
    universe = _factor_universe()
    strategy = FrameworkStrategy(
        alpha=FormulaFactorAlphaModel(
            tokens=["MOM20"], min_history=60, long_quantile=0.6
        ),
        portfolio_construction=OptimizerPCM(
            method=OptimizeMethod.HRP, rebalance_period=timedelta(days=5)
        ),
        execution=ImmediateExecutionModel(),
    )
    result = PortfolioBacktestEngine(
        _portfolio_config(initial_cash=1_000_000.0)
    ).run(strategy, universe)

    assert len(result.symbols) == 5
    assert result.fills, "因子 alpha 必须产生成交"
    assert framework_tearsheet_keys() <= set(result.report)
    assert result.report["per_symbol_metrics"]
    assert len(result.daily_results) > 0


def framework_tearsheet_keys() -> set[str]:
    return {"equity_curve", "drawdown_series", "monthly_returns", "pnl_distribution", "metrics"}
