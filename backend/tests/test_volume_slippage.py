"""
成交量约束滑点 + 部分成交测试（Wave K-b / K6）

验收（契约 waveKb §4.2）：
  - 单 bar 成交量上限
  - 部分成交后挂单残留
  - 同 order_id 多笔 Fill 的回合配对
另加最重要的一条：默认滑点模型 `fill_limit` 不限量 → 既有行为零变化。
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.broker import OrderStatus, SimulatedBroker
from app.engine.backtest.commission import USCommissionModel
from app.engine.backtest.metrics import compute_metrics
from app.engine.backtest.roundtrips import build_round_trips
from app.engine.backtest.slippage import (
    DEFAULT_VOLUME_LIMIT,
    FillLimit,
    FixedSlippage,
    MarketImpactSlippage,
    NoSlippage,
    VolumeShareSlippage,
    VolumeSlippage,
    get_slippage_model,
)

BAR_VOLUME = 100_000
START = datetime(2024, 1, 2, tzinfo=UTC)


def _bar(
    day: int = 0,
    open_: float = 100.0,
    close: float = 100.0,
    volume: int = BAR_VOLUME,
    symbol: str = "AAPL",
) -> Bar:
    return Bar(
        time=START + timedelta(days=day),
        symbol=symbol,
        market=Market.US,
        frequency=Frequency.DAY_1,
        open=open_,
        high=max(open_, close) + 1,
        low=min(open_, close) - 1,
        close=close,
        volume=volume,
    )


def _broker(slippage, cash: float = 10_000_000.0) -> SimulatedBroker:
    return SimulatedBroker(
        initial_cash=cash,
        market=Market.US,
        commission_model=USCommissionModel(),
        slippage_model=slippage,
    )


# ── 默认不限量：既有模型行为不变 ────────────────────────────────

@pytest.mark.parametrize(
    "model",
    [FixedSlippage(), VolumeSlippage(), NoSlippage(), get_slippage_model(Market.US)],
    ids=["fixed", "volume_pct", "none", "default_us"],
)
def test_existing_models_do_not_limit_fill_quantity(model) -> None:
    """回归红线：既有三个模型必须仍然不限量，否则基线立刻红。"""
    limit = model.fill_limit(order_qty=10**9, bar=_bar(volume=1))

    assert limit.max_qty == 10**9
    assert limit.reason is None


@pytest.mark.parametrize(
    "model",
    [FixedSlippage(), VolumeSlippage(), NoSlippage()],
    ids=["fixed", "volume_pct", "none"],
)
def test_apply_qty_defaults_to_apply(model) -> None:
    """未覆盖 apply_qty 的模型必须与 apply 给出完全相同的价格。"""
    bar = _bar()
    assert model.apply_qty(100.0, "BUY", bar, 5_000) == model.apply(100.0, "BUY", bar)


def test_fill_limit_is_immutable() -> None:
    limit = FillLimit(max_qty=10)
    with pytest.raises((AttributeError, TypeError)):
        limit.max_qty = 20  # type: ignore[misc]


# ── VolumeShareSlippage：上限与冲击 ─────────────────────────────

def test_volume_share_caps_at_volume_limit() -> None:
    model = VolumeShareSlippage(volume_limit=0.025)

    limit = model.fill_limit(order_qty=10_000, bar=_bar(volume=BAR_VOLUME))

    assert limit.max_qty == 2_500          # 2.5% × 100_000
    assert limit.reason is not None
    assert "成交量约束" in limit.reason


def test_volume_share_does_not_cap_small_orders() -> None:
    model = VolumeShareSlippage(volume_limit=0.025)

    limit = model.fill_limit(order_qty=100, bar=_bar(volume=BAR_VOLUME))

    assert limit.max_qty == 100
    assert limit.reason is None


def test_volume_share_zero_volume_bar_blocks_fill() -> None:
    model = VolumeShareSlippage()
    assert model.fill_limit(order_qty=100, bar=_bar(volume=0)).max_qty == 0


def test_volume_share_impact_is_quadratic_in_share() -> None:
    model = VolumeShareSlippage(volume_limit=0.5, price_impact=0.1)
    bar = _bar(volume=BAR_VOLUME)

    # 占比 10% → 冲击 = 0.1 × 0.1² = 0.001 → 100 × 1.001
    price = model.apply_qty(100.0, "BUY", bar, qty=10_000)

    assert price == pytest.approx(100.0 * (1 + 0.1 * 0.1**2))


def test_volume_share_impact_direction() -> None:
    model = VolumeShareSlippage(volume_limit=0.5, price_impact=0.1)
    bar = _bar(volume=BAR_VOLUME)

    buy = model.apply_qty(100.0, "BUY", bar, qty=10_000)
    sell = model.apply_qty(100.0, "SELL", bar, qty=10_000)

    assert buy > 100.0 > sell
    assert buy - 100.0 == pytest.approx(100.0 - sell)


def test_volume_share_larger_order_costs_more() -> None:
    model = VolumeShareSlippage(volume_limit=0.5, price_impact=0.1)
    bar = _bar(volume=BAR_VOLUME)

    small = model.apply_qty(100.0, "BUY", bar, qty=1_000)
    large = model.apply_qty(100.0, "BUY", bar, qty=20_000)

    assert large > small > 100.0


def test_volume_share_apply_without_qty_uses_worst_case() -> None:
    """不知道数量时按 volume_limit 估计，绝不低估冲击。"""
    model = VolumeShareSlippage(volume_limit=0.1, price_impact=0.1)
    bar = _bar(volume=BAR_VOLUME)

    assert model.apply(100.0, "BUY", bar) == pytest.approx(
        model.apply_qty(100.0, "BUY", bar, qty=BAR_VOLUME)
    )


# ── MarketImpactSlippage：平方根律 ─────────────────────────────

def test_market_impact_is_sqrt_in_share() -> None:
    model = MarketImpactSlippage(volume_limit=0.5, impact_coef=0.02)
    bar = _bar(volume=BAR_VOLUME)

    price = model.apply_qty(100.0, "BUY", bar, qty=10_000)   # 占比 10%

    assert price == pytest.approx(100.0 * (1 + 0.02 * math.sqrt(0.1)))


def test_market_impact_dominates_quadratic_for_small_orders() -> None:
    """平方根律不会像平方形式那样低估小单成本 —— 这正是它存在的理由。"""
    bar = _bar(volume=BAR_VOLUME)
    sqrt_model = MarketImpactSlippage(volume_limit=0.5, impact_coef=0.02)
    square_model = VolumeShareSlippage(volume_limit=0.5, price_impact=0.1)

    qty = 500   # 占比 0.5%
    assert sqrt_model.apply_qty(100.0, "BUY", bar, qty) > square_model.apply_qty(
        100.0, "BUY", bar, qty
    )


def test_market_impact_shares_the_volume_cap() -> None:
    model = MarketImpactSlippage(volume_limit=0.025)
    assert model.fill_limit(order_qty=10_000, bar=_bar(volume=BAR_VOLUME)).max_qty == 2_500


# ── 参数校验 ──────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
def test_volume_limit_must_be_in_unit_interval(bad: float) -> None:
    with pytest.raises(ValueError, match="volume_limit"):
        VolumeShareSlippage(volume_limit=bad)


def test_price_impact_must_be_non_negative() -> None:
    with pytest.raises(ValueError, match="price_impact"):
        VolumeShareSlippage(price_impact=-0.1)


def test_impact_coef_must_be_non_negative() -> None:
    with pytest.raises(ValueError, match="impact_coef"):
        MarketImpactSlippage(impact_coef=-0.1)


def test_default_volume_limit_matches_zipline() -> None:
    assert DEFAULT_VOLUME_LIMIT == 0.025
    assert VolumeShareSlippage().volume_limit == DEFAULT_VOLUME_LIMIT


# ── 部分成交 ──────────────────────────────────────────────────

def test_partial_fill_produces_capped_quantity() -> None:
    broker = _broker(VolumeShareSlippage(volume_limit=0.025, price_impact=0.0))
    broker.buy("AAPL", 10_000)

    fills = broker.process_bar(_bar(volume=BAR_VOLUME))

    assert len(fills) == 1
    assert fills[0].qty == 2_500
    assert broker.positions.get("AAPL").qty == 2_500


def test_partial_fill_leaves_remainder_pending() -> None:
    broker = _broker(VolumeShareSlippage(volume_limit=0.025, price_impact=0.0))
    order = broker.buy("AAPL", 10_000)

    broker.process_bar(_bar(volume=BAR_VOLUME))

    assert order.status == OrderStatus.PARTIAL
    assert order.qty == 7_500          # 残量
    assert order.filled_qty == 2_500   # 累计已成交
    assert broker.snapshot({"AAPL": 100.0})["pending_orders"] == 1


def test_partial_fill_completes_across_multiple_bars() -> None:
    broker = _broker(VolumeShareSlippage(volume_limit=0.025, price_impact=0.0))
    order = broker.buy("AAPL", 10_000)

    for day in range(4):
        broker.process_bar(_bar(day=day, volume=BAR_VOLUME))

    assert order.status == OrderStatus.FILLED
    assert order.filled_qty == 10_000
    assert broker.positions.get("AAPL").qty == 10_000
    assert len(broker.fills) == 4          # 2500 × 4
    assert broker.snapshot({"AAPL": 100.0})["pending_orders"] == 0


def test_partial_fill_commission_accumulates_across_fills() -> None:
    broker = _broker(VolumeShareSlippage(volume_limit=0.025, price_impact=0.0))
    order = broker.buy("AAPL", 10_000)

    for day in range(4):
        broker.process_bar(_bar(day=day, volume=BAR_VOLUME))

    assert order.commission == pytest.approx(sum(f.commission for f in broker.fills))


def test_all_partial_fills_share_one_order_id() -> None:
    broker = _broker(VolumeShareSlippage(volume_limit=0.025, price_impact=0.0))
    order = broker.buy("AAPL", 10_000)

    for day in range(4):
        broker.process_bar(_bar(day=day, volume=BAR_VOLUME))

    assert {f.order_id for f in broker.fills} == {order.order_id}


def test_zero_volume_bar_keeps_order_pending_without_filling() -> None:
    broker = _broker(VolumeShareSlippage())
    order = broker.buy("AAPL", 1_000)

    fills = broker.process_bar(_bar(volume=0))

    assert fills == []
    assert order.status == OrderStatus.PENDING
    assert order.reject_reason is not None
    assert broker.snapshot({"AAPL": 100.0})["pending_orders"] == 1


def test_partial_sell_leaves_remainder_pending() -> None:
    broker = _broker(VolumeShareSlippage(volume_limit=0.025, price_impact=0.0))
    broker.positions.buy("AAPL", 10_000, 90.0)

    order = broker.sell("AAPL", 10_000)
    fills = broker.process_bar(_bar(day=1, open_=100.0, volume=BAR_VOLUME))

    assert fills[0].qty == 2_500
    assert order.status == OrderStatus.PARTIAL
    assert broker.positions.get("AAPL").qty == 7_500


def test_unlimited_model_never_marks_order_partial() -> None:
    """默认模型下 PARTIAL 状态永远不该出现 —— 基线的隐含假设。"""
    broker = _broker(NoSlippage())
    order = broker.buy("AAPL", 10_000)

    broker.process_bar(_bar(volume=1))   # 成交量只有 1 也照样全额成交

    assert order.status == OrderStatus.FILLED
    assert order.qty == 10_000
    assert broker.snapshot({"AAPL": 100.0})["pending_orders"] == 0


# ── 同 order_id 多笔 Fill 的回合配对 ───────────────────────────

def _fill_rows(broker: SimulatedBroker) -> list[dict]:
    return [
        {
            "order_id": f.order_id,
            "symbol": f.symbol,
            "side": f.side.value,
            "qty": f.qty,
            "price": f.price,
            "commission": f.commission,
            "filled_at": f.filled_at.isoformat(),
            "realized_pnl": f.realized_pnl,
            "entry_tag": f.entry_tag,
            "exit_reason": f.exit_reason,
        }
        for f in broker.fills
    ]


def test_round_trips_pair_multi_fill_orders() -> None:
    """
    一个 BUY 分 4 笔成交、一个 SELL 分 4 笔成交 → 回合配对必须仍然成立，
    且 sum(trip.pnl) 与券商 realized_pnl 口径一致（roundtrips.py 的关键不变量）。
    """
    broker = _broker(VolumeShareSlippage(volume_limit=0.025, price_impact=0.0))

    broker.buy("AAPL", 10_000, entry_tag="breakout")
    for day in range(4):
        broker.process_bar(_bar(day=day, open_=100.0, volume=BAR_VOLUME))

    broker.sell("AAPL", 10_000, exit_reason="take_profit")
    for day in range(4, 8):
        broker.process_bar(_bar(day=day, open_=120.0, volume=BAR_VOLUME))

    fills = _fill_rows(broker)
    trips = build_round_trips(fills)

    assert len(broker.fills) == 8
    assert sum(t.qty for t in trips) == 10_000
    assert all(t.entry_tag == "breakout" for t in trips)
    assert all(t.exit_reason == "take_profit" for t in trips)

    realized_total = sum(f["realized_pnl"] for f in fills)
    assert sum(t.pnl for t in trips) == pytest.approx(realized_total)
    assert all(t.pnl > 0 for t in trips)   # 100 买、120 卖


def test_metrics_counts_each_partial_sell_fill() -> None:
    """metrics 按卖出 Fill 计交易笔数；部分成交下同一订单会记多笔，且总盈亏守恒。"""
    broker = _broker(VolumeShareSlippage(volume_limit=0.025, price_impact=0.0))
    broker.buy("AAPL", 10_000)
    for day in range(4):
        broker.process_bar(_bar(day=day, open_=100.0, volume=BAR_VOLUME))
    broker.sell("AAPL", 10_000)
    for day in range(4, 8):
        broker.process_bar(_bar(day=day, open_=120.0, volume=BAR_VOLUME))

    fills = _fill_rows(broker)
    equity = pd.Series(
        [10_000_000.0 + i for i in range(8)],
        index=pd.DatetimeIndex([START + timedelta(days=i) for i in range(8)]),
    )

    metrics = compute_metrics(equity, fills, initial_cash=10_000_000.0)

    assert metrics.total_trades == 4       # 4 笔卖出 Fill
    assert metrics.win_rate == 1.0


# ── add_cash（K8 分红现金流所需） ──────────────────────────────

def test_add_cash_increases_balance() -> None:
    broker = _broker(NoSlippage(), cash=1_000.0)
    assert broker.add_cash(250.0) == 1_250.0
    assert broker.cash == 1_250.0


def test_add_cash_rejects_overdraft() -> None:
    broker = _broker(NoSlippage(), cash=100.0)
    with pytest.raises(ValueError, match="现金不足"):
        broker.add_cash(-500.0)


# ── 成交量上限 × 现金约束同时生效时的账目守恒 ──────────────────
#
# 回归用例：曾经 `remaining_qty` 只由成交量缺口算出，而 `_settle_buy_cash`
# 在现金不足时还会把成交量再压低一档 —— 被现金砍掉的那部分既不成交、
# 也不重新挂单、也不拒单，凭空从账上消失（1000 股的单只留下 97+500）。

def test_volume_cap_and_cash_constraint_together_leave_no_untracked_shares() -> None:
    # Arrange: 成交量上限 50%（=500 股），但现金只买得起约 97 股
    broker = _broker(VolumeShareSlippage(volume_limit=0.5), cash=10_000.0)
    order = broker.buy("AAPL", 1000)

    # Act
    fills = broker.process_bar(_bar(volume=1000))

    # Assert: 每一股的去向都有明确记录
    filled = sum(f.qty for f in fills)
    requeued = sum(p.qty for p in broker._pending)
    assert filled > 0
    assert requeued == 500                      # 成交量缺口 → 残单重新挂回
    dropped = 1000 - filled - requeued          # 现金缺口 → 按既有语义作废
    assert dropped > 0
    # 作废的部分必须留痕，不允许静默丢失
    assert order.reject_reason is not None
    assert str(dropped) in order.reject_reason
    assert "现金不足" in order.reject_reason


def test_cash_constrained_buy_without_volume_cap_keeps_legacy_semantics() -> None:
    # Arrange: 无成交量上限时，现金不足的部分按既有语义作废且不留残单
    broker = _broker(NoSlippage(), cash=10_000.0)
    order = broker.buy("AAPL", 1000)

    # Act
    fills = broker.process_bar(_bar())

    # Assert
    assert len(fills) == 1
    assert broker._pending == []                # 不重新挂单（与做空/K6 上线前一致）
    assert order.status == OrderStatus.FILLED
    assert order.remaining_qty == 0


# ── 拒单不得重新挂回队列 ────────────────────────────────────────
#
# 回归用例：`process_bar` 曾只看 `_try_fill` 是否返回 Fill，返回 None 就无条件
# 挂回队列 —— 即使 `_try_fill` 已把订单标成 REJECTED。结果是对外报了「拒绝」，
# 订单却留在队列里，下一根 bar 可能真的成交。

def test_rejected_order_is_not_requeued() -> None:
    # Arrange: 现金买不起一股 → _try_fill 内部拒单
    broker = _broker(NoSlippage(), cash=1.0)
    order = broker.buy("AAPL", 100)

    # Act
    fills = broker.process_bar(_bar(open_=100.0))

    # Assert
    assert fills == []
    assert order.status == OrderStatus.REJECTED
    assert broker._pending == []                 # 不得留在队列里等下一根 bar

    # 下一根 bar 即使价格变得可负担，也不该凭空成交
    assert broker.process_bar(_bar(day=1, open_=0.5)) == []


# ── 冲击必须按**实际成交量**算，而非成交量上限 ──────────────────
#
# 回归用例：合并 K-a/K-b 时 `_try_fill` 里 `apply_qty(...)` 的结果被紧随其后的
# `apply(...)` 无条件覆盖，导致按量冲击从未生效 —— 所有单子一律按 volume_limit
# 上限计冲击。占比是平方项，1% 的小单按 2.5% 上限算要高估 6.25 倍。
#
# 之所以没被既有测试抓到：它们的委托量都**大于**上限，成交量恰好等于上限，
# 两条路径结果相同。这里专门构造「成交量远小于上限」的情形。

def test_market_impact_uses_actual_fill_share_not_the_volume_cap() -> None:
    # Arrange: 上限 50%（=500 股），但只买 10 股 → 实际占比 1%
    model = VolumeShareSlippage(volume_limit=0.5, price_impact=0.1)
    broker = _broker(model, cash=10_000_000.0)
    broker.buy("AAPL", 10)

    # Act
    fills = broker.process_bar(_bar(open_=100.0, volume=1_000))

    # Assert
    assert len(fills) == 1
    # 实际占比 0.01 → 冲击 0.1 × 0.01² = 1e-5 → 100 × (1 + 1e-5)
    expected = 100.0 * (1 + 0.1 * 0.01 ** 2)
    assert fills[0].price == pytest.approx(expected, rel=1e-12)

    # 而按上限估的价会高得多，绝不能等于它
    worst_case = 100.0 * (1 + 0.1 * 0.5 ** 2)
    assert fills[0].price < worst_case
    assert model.apply(100.0, "BUY", _bar(volume=1_000)) == pytest.approx(worst_case)


def test_market_impact_matches_cap_estimate_when_order_exceeds_cap() -> None:
    # Arrange: 委托量大于上限 → 成交量 = 上限 → 两种算法应当一致
    model = VolumeShareSlippage(volume_limit=0.5, price_impact=0.1)
    broker = _broker(model, cash=10_000_000.0)
    broker.buy("AAPL", 5_000)

    # Act
    fills = broker.process_bar(_bar(open_=100.0, volume=1_000))

    # Assert
    assert fills[0].price == pytest.approx(model.apply(100.0, "BUY", _bar(volume=1_000)))
