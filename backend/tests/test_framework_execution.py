"""L3 执行模型测试（Wave L-b）

对应契约 docs/contracts/waveLb-risk-execution-models.md §三 与 §五.3/§五.4。
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.broker import OrderSide, OrderStatus
from app.engine.backtest.commission import CommissionResult
from app.engine.backtest.portfolio_broker import PortfolioBroker
from app.engine.backtest.portfolio_engine import (
    PortfolioBacktestConfig,
    PortfolioBacktestEngine,
)
from app.engine.backtest.slippage import NoSlippage, VolumeShareSlippage
from app.engine.framework import (
    ExecutionModel,
    FormulaFactorAlphaModel,
    FrameworkStrategy,
    ImmediateExecutionModel,
    MaximumDrawdownPortfolio,
    OptimizerPCM,
    PortfolioTarget,
    SpreadExecution,
    StandardDeviationExecution,
    VolumeWeightedAveragePriceExecution,
)
from app.engine.portfolio.optimizer import OptimizeMethod
from app.strategy.context import PortfolioContext

START = datetime(2024, 1, 2, tzinfo=UTC)
_DAY = timedelta(days=1)


class _ZeroCommission:
    def calculate(self, price: float, qty: int, side: str) -> CommissionResult:
        return CommissionResult(commission=0.0, fees=0.0, total=0.0)


def _bar(
    symbol: str,
    price: float,
    ts: datetime,
    *,
    volume: float = 1_000_000.0,
    spread: float = 0.0,
) -> Bar:
    """spread 是「(high-low)/close」的目标值，用来驱动 SpreadExecution。"""
    half = price * spread / 2.0
    return Bar(
        time=ts,
        symbol=symbol,
        market=Market.US,
        frequency=Frequency.DAY_1,
        open=price,
        high=price + half,
        low=price - half,
        close=price,
        volume=volume,
    )


def _frame(closes: list[float]) -> pd.DataFrame:
    index = pd.DatetimeIndex([START + timedelta(days=i) for i in range(len(closes))])
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1_000_000.0] * len(closes),
        },
        index=index,
    )


def _broker(cash: float = 10_000_000.0, slippage=None) -> PortfolioBroker:
    return PortfolioBroker(
        initial_cash=cash,
        market=Market.US,
        commission_model=_ZeroCommission(),
        slippage_model=slippage or NoSlippage(),
    )


def _context(
    broker: PortfolioBroker,
    prices: dict[str, float],
    ts: datetime = START,
    *,
    volume: float = 1_000_000.0,
    spread: float = 0.0,
    histories: dict[str, pd.DataFrame] | None = None,
    priced: bool = True,
) -> PortfolioContext:
    bars = (
        {s: _bar(s, p, ts, volume=volume, spread=spread) for s, p in prices.items()}
        if priced
        else {}
    )
    return PortfolioContext(
        time=ts,
        bars=bars,
        symbols=sorted(prices),
        broker=broker,
        histories=histories or {s: _frame([p] * 3) for s, p in prices.items()},
        market=Market.US,
    )


def _ctx(prices: dict[str, float], **kwargs) -> PortfolioContext:
    cash = kwargs.pop("cash", 10_000_000.0)
    broker = _broker(cash)
    broker.process_bars(START, {s: _bar(s, p, START) for s, p in prices.items()})
    return _context(broker, prices, **kwargs)


def _totals(orders) -> dict[str, int]:
    """symbol → 带符号委托量（BUY 为正，SELL 为负）。"""
    out: dict[str, int] = {}
    for order in orders:
        out[order.symbol] = out.get(order.symbol, 0) + (
            order.qty if order.side is OrderSide.BUY else -order.qty
        )
    return out


# ── 0. 模块变包后的导入路径不变 ───────────────────────────────


def test_module_to_package_keeps_legacy_import_paths():
    from app.engine.framework.execution import ExecutionModel as FromPackage
    from app.engine.framework.execution import ImmediateExecutionModel as ImmediateFromPackage

    assert FromPackage is ExecutionModel
    assert ImmediateFromPackage is ImmediateExecutionModel


def test_immediate_execution_still_emits_the_full_diff():
    """默认执行模型行为必须逐笔不变（回归红线）。"""
    ctx = _ctx({"AAPL": 100.0})

    orders = ImmediateExecutionModel().execute(ctx, [PortfolioTarget("AAPL", 500)])

    assert _totals(orders) == {"AAPL": 500}


# ── 1. VolumeWeightedAveragePriceExecution ────────────────────


def test_vwap_caps_each_bar_at_the_volume_share():
    # Arrange: bar 成交量 1,000,000 × 1% = 10,000 股上限，目标 25,000 股
    ctx = _ctx({"AAPL": 100.0})

    orders = VolumeWeightedAveragePriceExecution(0.01).execute(
        ctx, [PortfolioTarget("AAPL", 25_000)]
    )

    assert _totals(orders) == {"AAPL": 10_000}


def test_vwap_works_the_remainder_over_following_bars_until_target_is_met():
    """契约 §五.3：残量下一 bar 继续，累计成交 == 目标。"""
    broker = _broker()
    broker.process_bars(START, {"AAPL": _bar("AAPL", 100.0, START)})
    model = VolumeWeightedAveragePriceExecution(0.01)
    target = [PortfolioTarget("AAPL", 25_000)]

    submitted = 0
    for day in range(5):
        ts = START + day * _DAY
        ctx = _context(broker, {"AAPL": 100.0}, ts)
        for order in model.execute(ctx, target):
            submitted += order.qty if order.side is OrderSide.BUY else -order.qty
            ctx.submit(order)
        broker.process_bars(ts + _DAY, {"AAPL": _bar("AAPL", 100.0, ts + _DAY)})

    assert submitted == 25_000                                   # 累计委托 == 目标
    assert broker.positions.get("AAPL").qty == 25_000            # 累计成交 == 目标


def test_vwap_stops_emitting_orders_once_the_target_is_reached():
    broker = _broker()
    broker.process_bars(START, {"AAPL": _bar("AAPL", 100.0, START)})
    model = VolumeWeightedAveragePriceExecution(0.01)
    target = [PortfolioTarget("AAPL", 5_000)]

    ctx = _context(broker, {"AAPL": 100.0}, START)
    for order in model.execute(ctx, target):
        ctx.submit(order)
    broker.process_bars(START + _DAY, {"AAPL": _bar("AAPL", 100.0, START + _DAY)})

    later = model.execute(_context(broker, {"AAPL": 100.0}, START + _DAY), target)
    assert later == []


def test_vwap_supersedes_a_working_target_with_the_newest_instruction():
    broker = _broker()
    broker.process_bars(START, {"AAPL": _bar("AAPL", 100.0, START)})
    model = VolumeWeightedAveragePriceExecution(0.01)

    # 第一次：目标 25,000，本 bar 只放行 10,000 并成交
    ctx = _context(broker, {"AAPL": 100.0}, START)
    for order in model.execute(ctx, [PortfolioTarget("AAPL", 25_000)]):
        ctx.submit(order)
    broker.process_bars(START + _DAY, {"AAPL": _bar("AAPL", 100.0, START + _DAY)})
    assert broker.positions.get("AAPL").qty == 10_000

    # 第二次：目标下调到 12,000 —— 只该补 2,000，而不是继续推进旧的 25,000
    orders = model.execute(
        _context(broker, {"AAPL": 100.0}, START + _DAY), [PortfolioTarget("AAPL", 12_000)]
    )

    assert _totals(orders) == {"AAPL": 2_000}


def test_vwap_does_not_double_order_while_an_earlier_slice_is_still_in_flight():
    """在途委托必须计入 diff：只看已成交持仓会在下一根 bar 重复补同样的量。"""
    broker = _broker(slippage=VolumeShareSlippage(0.002, 0.0))   # 撮合层只放行 2,000
    broker.process_bars(START, {"AAPL": _bar("AAPL", 100.0, START)})
    model = VolumeWeightedAveragePriceExecution(0.01)            # 执行层放行 10,000
    target = [PortfolioTarget("AAPL", 10_000)]

    ctx = _context(broker, {"AAPL": 100.0}, START)
    for order in model.execute(ctx, target):                     # 委托 10,000
        ctx.submit(order)
    broker.process_bars(START + _DAY, {"AAPL": _bar("AAPL", 100.0, START + _DAY)})
    assert broker.positions.get("AAPL").qty == 2_000             # 只成交 2,000，残量 8,000 在途

    orders = model.execute(_context(broker, {"AAPL": 100.0}, START + _DAY), target)

    assert orders == []                                          # 在途已覆盖全部剩余目标


def test_vwap_skips_symbols_without_a_bar_this_timepoint():
    """停牌标的没有成交量可参照，本 bar 不下单（而不是按上一根的量硬拆）。"""
    ctx = _ctx({"AAPL": 100.0}, priced=False)

    orders = VolumeWeightedAveragePriceExecution(0.01).execute(
        ctx, [PortfolioTarget("AAPL", 25_000)]
    )

    assert orders == []


def test_vwap_skips_when_the_volume_share_rounds_down_to_zero():
    ctx = _ctx({"AAPL": 100.0}, volume=50.0)      # 50 × 1% = 0.5 → 取整为 0

    orders = VolumeWeightedAveragePriceExecution(0.01).execute(
        ctx, [PortfolioTarget("AAPL", 1_000)]
    )

    assert orders == []


def test_vwap_caps_sells_symmetrically():
    broker = _broker()
    broker.buy("AAPL", 25_000, Market.US)
    broker.process_bars(START, {"AAPL": _bar("AAPL", 100.0, START)})

    orders = VolumeWeightedAveragePriceExecution(0.01).execute(
        _context(broker, {"AAPL": 100.0}), [PortfolioTarget("AAPL", 0)]
    )

    assert _totals(orders) == {"AAPL": -10_000}


def test_vwap_rejects_out_of_range_percent():
    with pytest.raises(ValueError, match="max_order_percent_volume"):
        VolumeWeightedAveragePriceExecution(0.0)


# ── 2. StandardDeviationExecution ─────────────────────────────

#: 60 根样本：前 59 根在 100 上下，最后一根显著偏离
_FLAT = [100.0] * 30 + [102.0] * 15 + [98.0] * 14


def _std_history(last: float) -> dict[str, pd.DataFrame]:
    return {"AAPL": _frame([*_FLAT, last])}


def test_std_dev_buys_only_when_the_price_is_far_below_the_mean():
    ctx = _ctx({"AAPL": 80.0}, histories=_std_history(80.0))

    orders = StandardDeviationExecution(period=60, deviations=2.0).execute(
        ctx, [PortfolioTarget("AAPL", 500)]
    )

    assert _totals(orders) == {"AAPL": 500}       # 便宜且是买 → 成交


def test_std_dev_refuses_to_buy_at_a_normal_price():
    ctx = _ctx({"AAPL": 100.0}, histories=_std_history(100.0))

    orders = StandardDeviationExecution(period=60, deviations=2.0).execute(
        ctx, [PortfolioTarget("AAPL", 500)]
    )

    assert orders == []


def test_std_dev_sells_only_when_the_price_is_far_above_the_mean():
    broker = _broker()
    broker.buy("AAPL", 500, Market.US)
    broker.process_bars(START, {"AAPL": _bar("AAPL", 100.0, START)})
    ctx = _context(broker, {"AAPL": 130.0}, histories=_std_history(130.0))

    orders = StandardDeviationExecution(period=60, deviations=2.0).execute(
        ctx, [PortfolioTarget("AAPL", 0)]
    )

    assert _totals(orders) == {"AAPL": -500}


def test_std_dev_refuses_to_sell_below_the_mean():
    broker = _broker()
    broker.buy("AAPL", 500, Market.US)
    broker.process_bars(START, {"AAPL": _bar("AAPL", 100.0, START)})
    ctx = _context(broker, {"AAPL": 80.0}, histories=_std_history(80.0))

    orders = StandardDeviationExecution(period=60, deviations=2.0).execute(
        ctx, [PortfolioTarget("AAPL", 0)]
    )

    assert orders == []


def test_std_dev_waits_for_enough_history():
    ctx = _ctx({"AAPL": 50.0}, histories={"AAPL": _frame([100.0] * 10)})

    orders = StandardDeviationExecution(period=60, deviations=2.0).execute(
        ctx, [PortfolioTarget("AAPL", 500)]
    )

    assert orders == []


def test_std_dev_does_not_trade_a_symbol_without_history():
    """历史缺失是配置错误，不能默默按「无偏离」放行。"""
    ctx = _ctx({"AAPL": 100.0}, histories={})

    orders = StandardDeviationExecution(period=60).execute(ctx, [PortfolioTarget("AAPL", 500)])

    assert orders == []


def test_std_dev_rejects_invalid_params():
    with pytest.raises(ValueError, match="period"):
        StandardDeviationExecution(period=1)
    with pytest.raises(ValueError, match="deviations"):
        StandardDeviationExecution(deviations=0.0)


# ── 3. SpreadExecution ────────────────────────────────────────


def test_spread_execution_trades_when_the_spread_is_tight():
    ctx = _ctx({"AAPL": 100.0}, spread=0.002)

    orders = SpreadExecution(accepted_spread_percent=0.005).execute(
        ctx, [PortfolioTarget("AAPL", 500)]
    )

    assert _totals(orders) == {"AAPL": 500}


def test_spread_execution_waits_when_the_spread_is_wide():
    ctx = _ctx({"AAPL": 100.0}, spread=0.02)

    orders = SpreadExecution(accepted_spread_percent=0.005).execute(
        ctx, [PortfolioTarget("AAPL", 500)]
    )

    assert orders == []


def test_spread_execution_skips_symbols_without_a_bar():
    ctx = _ctx({"AAPL": 100.0}, priced=False)

    orders = SpreadExecution().execute(ctx, [PortfolioTarget("AAPL", 500)])

    assert orders == []


def test_spread_execution_rejects_non_positive_threshold():
    with pytest.raises(ValueError, match="accepted_spread_percent"):
        SpreadExecution(accepted_spread_percent=0.0)


def test_spread_execution_skips_bars_with_an_illegal_close():
    """收盘价非法时不能当成「价差 0」放行，只能跳过。"""
    broker = _broker()
    broker.process_bars(START, {"AAPL": _bar("AAPL", 100.0, START)})
    ctx = _context(broker, {"AAPL": 100.0})
    ctx.bars["AAPL"] = Bar(
        time=START, symbol="AAPL", market=Market.US, frequency=Frequency.DAY_1,
        open=100.0, high=100.0, low=100.0, close=0.0, volume=1_000.0,
    )
    ctx.broker._last_prices["AAPL"] = 100.0                      # noqa: SLF001

    assert SpreadExecution().execute(ctx, [PortfolioTarget("AAPL", 500)]) == []


# ── 4. L3 拆单 × K6 撮合层成交量约束（契约 §3.1）─────────────


def test_vwap_execution_layered_on_volume_share_slippage_conserves_shares():
    """执行层主动拆单 + 撮合层被动上限：每一股的去向都必须有账可查。

    守恒式：累计成交 + 仍在挂的残量 + 显式作废（拒单/撤单）== 累计委托。
    """
    # Arrange: 执行层按 10% 拆单（每 bar 最多委托 10 万股），撮合层每张单
    # 每 bar 只放行 0.2%（2,000 股）；现金给足，把「作废」这一路彻底排除
    broker = _broker(cash=1_000_000_000.0, slippage=VolumeShareSlippage(0.002, 0.0))
    broker.process_bars(START, {"AAPL": _bar("AAPL", 100.0, START)})
    model = VolumeWeightedAveragePriceExecution(0.10)
    target = [PortfolioTarget("AAPL", 300_000)]

    ordered = 0
    submitted = []
    emitted: list[int] = []
    for day in range(5):
        ts = START + day * _DAY
        ctx = _context(broker, {"AAPL": 100.0}, ts)
        for order in model.execute(ctx, target):
            ordered += order.qty
            emitted.append(order.qty)
            submitted.append(ctx.submit(order))
        broker.process_bars(ts + _DAY, {"AAPL": _bar("AAPL", 100.0, ts + _DAY)})

    # Assert
    filled = broker.positions.get("AAPL").qty
    pending = sum(o.qty for o in broker._pending)                       # noqa: SLF001
    voided = sum(o.qty for o in submitted if o.status is OrderStatus.REJECTED)
    assert ordered > 0
    assert voided == 0                                                  # 现金充足 → 无作废
    assert filled + pending == ordered                                  # 账目守恒
    assert 0 < filled < 300_000                                         # 撮合层确实在限速
    assert all(qty <= 100_000 for qty in emitted)                       # 执行层拆单上限生效
    # 执行层不会因为撮合层慢就重复补单：累计委托从不超过目标
    assert ordered <= 300_000


def test_vwap_never_exceeds_the_target_even_with_partial_fills():
    broker = _broker(cash=1_000_000_000.0, slippage=VolumeShareSlippage(0.02, 0.0))
    broker.process_bars(START, {"AAPL": _bar("AAPL", 100.0, START)})
    model = VolumeWeightedAveragePriceExecution(0.10)
    target = [PortfolioTarget("AAPL", 50_000)]

    for day in range(10):
        ts = START + day * _DAY
        ctx = _context(broker, {"AAPL": 100.0}, ts)
        for order in model.execute(ctx, target):
            ctx.submit(order)
        broker.process_bars(ts + _DAY, {"AAPL": _bar("AAPL", 100.0, ts + _DAY)})
        assert broker.positions.get("AAPL").qty <= 50_000

    assert broker.positions.get("AAPL").qty == 50_000


# ── 5. 端到端：因子 Alpha + HRP 优化 + 组合风控 + VWAP 执行 ────


_E2E_SYMBOLS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN"]


def _e2e_bars(n: int = 150) -> dict[str, list[Bar]]:
    """5 个标的、各 150 根 bar，走势彼此不同以便 HRP 能算出有意义的协方差。"""
    return {
        symbol: [
            _bar(
                symbol,
                round(100.0 + 10 * math.sin((i + k * 5) / 13.0) + i * (0.02 + 0.01 * k), 4),
                START + i * _DAY,
                volume=200_000.0,
            )
            for i in range(n)
        ]
        for k, symbol in enumerate(_E2E_SYMBOLS)
    }


def test_end_to_end_factor_alpha_hrp_portfolio_risk_and_vwap_execution():
    """契约 §五.4 的端到端组合：因子 Alpha + HRP + 组合回撤风控 + VWAP 拆单。"""
    strategy = FrameworkStrategy(
        alpha=FormulaFactorAlphaModel(tokens=["MOM20"], min_history=60, long_quantile=0.6),
        portfolio_construction=OptimizerPCM(
            method=OptimizeMethod.HRP, rebalance_period=timedelta(days=5)
        ),
        risk=MaximumDrawdownPortfolio(max_drawdown=0.10),
        execution=VolumeWeightedAveragePriceExecution(0.01),
    )
    config = PortfolioBacktestConfig(
        initial_cash=1_000_000.0,
        market=Market.US,
        slippage_model=NoSlippage(),
        commission_model=_ZeroCommission(),
    )

    result = PortfolioBacktestEngine(config).run(strategy, _e2e_bars())

    assert len(result.symbols) == 5
    assert result.fills, "端到端组合没有产生任何成交"
    assert result.final_value > 0
    # VWAP 拆单：bar 量 200,000 × 1% ⇒ 单笔委托上限 2,000 股
    assert all(f["qty"] <= 2_000 for f in result.fills)
    assert len(result.daily_results) > 0
