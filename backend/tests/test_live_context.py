"""实盘组合上下文 — Wave L-d 契约 §四 / §六 验收 2

三件事：
1. `LivePortfolioContext` 的公开接口与 `PortfolioContext` **完全一致**（防接口漂移）；
2. `target_weight` 在实盘上下文中产出与回测**逐股相同**的 diff 订单；
3. 账户快照非法（尤其 `portfolio_value <= 0`）时**造不出快照**，
   绝不允许退化成「净值 0 ⇒ 全部清仓」。
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.broker import OrderSide, OrderStatus
from app.engine.backtest.commission import CommissionResult
from app.engine.backtest.order_types import OrderType
from app.engine.backtest.portfolio_broker import PortfolioBroker
from app.engine.backtest.slippage import NoSlippage
from app.strategy.context import PortfolioContext
from app.strategy.live_context import AccountSnapshot, LivePortfolioContext

BASE_TIME = datetime(2024, 1, 2, tzinfo=UTC)
SYMBOLS = ["AAPL", "MSFT"]


# ── 夹具 ──────────────────────────────────────────────────────


class _ZeroCommission:
    def calculate(self, price: float, qty: int, side: str) -> CommissionResult:
        return CommissionResult(commission=0.0, fees=0.0, total=0.0)


def _bar(symbol: str, close: float, day: int = 0) -> Bar:
    return Bar(
        time=BASE_TIME + timedelta(days=day),
        symbol=symbol,
        market=Market.US,
        frequency=Frequency.DAY_1,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000,
    )


def _seeded_broker() -> PortfolioBroker:
    """跑两个时点、成交一笔 AAPL，得到一个有真实持仓与现金的回测券商。"""
    broker = PortfolioBroker(
        initial_cash=100_000.0,
        market=Market.US,
        commission_model=_ZeroCommission(),
        slippage_model=NoSlippage(),
    )
    t0 = BASE_TIME
    broker.process_bars(t0, {"AAPL": _bar("AAPL", 100.0), "MSFT": _bar("MSFT", 50.0)})
    broker.buy("AAPL", 100, Market.US)
    t1 = BASE_TIME + timedelta(days=1)
    broker.process_bars(
        t1, {"AAPL": _bar("AAPL", 110.0, 1), "MSFT": _bar("MSFT", 55.0, 1)}
    )
    return broker


def _snapshot_of(broker: PortfolioBroker, taken_at: datetime) -> AccountSnapshot:
    """把回测券商状态冻结成一份等价的实盘账户快照。"""
    return AccountSnapshot(
        cash=broker.cash,
        portfolio_value=broker.portfolio_value(),
        positions=broker.positions.net_quantities(),
        prices=broker.mark_prices(),
        avg_costs={s: broker.positions.get(s).avg_cost for s in SYMBOLS},
        taken_at=taken_at,
    )


def _histories() -> dict[str, pd.DataFrame]:
    return {
        s: pd.DataFrame(
            {"close": [100.0, 110.0], "volume": [1, 2]},
            index=pd.Index([BASE_TIME, BASE_TIME + timedelta(days=1)], name="time"),
        )
        for s in SYMBOLS
    }


def _contexts() -> tuple[PortfolioContext, LivePortfolioContext]:
    """一对状态等价的「回测上下文 / 实盘上下文」。"""
    broker = _seeded_broker()
    t1 = BASE_TIME + timedelta(days=1)
    bars = {"AAPL": _bar("AAPL", 110.0, 1), "MSFT": _bar("MSFT", 55.0, 1)}
    backtest_ctx = PortfolioContext(
        time=t1,
        bars=bars,
        symbols=list(SYMBOLS),
        broker=broker,
        histories=_histories(),
        market=Market.US,
    )
    live_ctx = LivePortfolioContext(
        snapshot=_snapshot_of(broker, t1),
        time=t1,
        bars=bars,
        symbols=list(SYMBOLS),
        histories=_histories(),
        market=Market.US,
    )
    return backtest_ctx, live_ctx


# ── 1. 接口完整性（防漂移）────────────────────────────────────


def _public_surface(cls: type) -> set[str]:
    """类的公开可调用面：方法 + property。"""
    surface: set[str] = set()
    for name in dir(cls):
        if name.startswith("_"):
            continue
        attr = inspect.getattr_static(cls, name, None)
        if callable(attr) or isinstance(attr, property):
            surface.add(name)
    return surface


def test_live_context_implements_every_public_portfolio_context_member():
    missing = _public_surface(PortfolioContext) - _public_surface(LivePortfolioContext)
    assert not missing, f"LivePortfolioContext 缺少回测上下文的公开成员: {sorted(missing)}"


def test_live_context_is_a_portfolio_context():
    """策略侧的类型标注是 PortfolioContext —— 实盘版必须能原样代入。"""
    assert issubclass(LivePortfolioContext, PortfolioContext)


def test_live_context_signatures_match_backtest_context():
    """同名方法的签名逐字一致，否则策略在两条路径上的调用方式会分叉。"""
    for name in sorted(_public_surface(PortfolioContext)):
        backtest_attr = inspect.getattr_static(PortfolioContext, name)
        live_attr = inspect.getattr_static(LivePortfolioContext, name)
        if isinstance(backtest_attr, property):
            assert isinstance(live_attr, property), f"{name} 在实盘版不是 property"
            continue
        assert inspect.signature(backtest_attr) == inspect.signature(live_attr), (
            f"{name} 的签名在实盘版发生漂移"
        )


# ── 2. 快照读取 ───────────────────────────────────────────────


def test_snapshot_readers_match_backtest_state():
    backtest_ctx, live_ctx = _contexts()
    assert live_ctx.cash == pytest.approx(backtest_ctx.cash)
    assert live_ctx.portfolio_value == pytest.approx(backtest_ctx.portfolio_value)
    assert live_ctx.current_prices == backtest_ctx.current_prices
    assert live_ctx.qty("AAPL") == backtest_ctx.qty("AAPL") == 100
    assert live_ctx.qty("MSFT") == backtest_ctx.qty("MSFT") == 0
    assert live_ctx.price("AAPL") == backtest_ctx.price("AAPL") == 110.0


def test_position_is_reconstructed_from_snapshot():
    _, live_ctx = _contexts()
    pos = live_ctx.position("AAPL")
    assert pos is not None
    assert pos.qty == 100
    assert pos.avg_cost == pytest.approx(110.0)   # next-bar open 成交价
    assert live_ctx.position("MSFT") is None


def test_price_falls_back_to_snapshot_when_symbol_has_no_bar():
    """停牌标的没有 bar，必须沿用快照里的最后已知价 —— 不能是 None/0。"""
    broker = _seeded_broker()
    t1 = BASE_TIME + timedelta(days=1)
    live_ctx = LivePortfolioContext(
        snapshot=_snapshot_of(broker, t1),
        time=t1,
        bars={"AAPL": _bar("AAPL", 110.0, 1)},      # MSFT 缺失
        symbols=list(SYMBOLS),
        histories=_histories(),
        market=Market.US,
    )
    assert live_ctx.bar("MSFT") is None
    assert live_ctx.price("MSFT") == 55.0


def test_history_accessors_read_the_injected_frames():
    _, live_ctx = _contexts()
    assert len(live_ctx.history("AAPL")) == 2
    assert list(live_ctx.close_series("AAPL", 1)) == [110.0]
    assert list(live_ctx.volume_series("MSFT", 1)) == [2]


# ── 3. 下单只记录意图 ─────────────────────────────────────────


def test_buy_returns_local_pending_order_and_collects_it():
    _, live_ctx = _contexts()
    order = live_ctx.buy("MSFT", 10)
    assert order is not None
    assert order.status is OrderStatus.PENDING
    assert order.side is OrderSide.BUY
    assert order.filled_price is None          # 实盘永远拿不到即时成交价
    assert live_ctx.pending_orders() == (order,)


def test_non_positive_qty_produces_no_order():
    _, live_ctx = _contexts()
    assert live_ctx.buy("MSFT", 0) is None
    assert live_ctx.sell("AAPL", -5) is None
    assert live_ctx.pending_orders() == ()


def test_submit_collects_a_prebuilt_order():
    """执行模型走的是 ctx.submit —— 它必须原样收集，不做二次加工。"""
    from app.engine.backtest.broker import Order

    _, live_ctx = _contexts()
    order = Order(symbol="AAPL", market=Market.US, side=OrderSide.SELL, qty=7)
    assert live_ctx.submit(order) is order
    assert live_ctx.pending_orders() == (order,)


def test_close_all_uses_snapshot_position():
    _, live_ctx = _contexts()
    order = live_ctx.close_all("AAPL", exit_reason="signal")
    assert order is not None
    assert (order.side, order.qty, order.exit_reason) == (OrderSide.SELL, 100, "signal")
    assert live_ctx.close_all("MSFT") is None


def test_buy_value_uses_snapshot_price():
    _, live_ctx = _contexts()
    order = live_ctx.buy_value("MSFT", 1_000.0)
    assert order is not None
    assert order.qty == 18                       # int(1000 / 55)


def test_limit_order_type_is_carried_through():
    _, live_ctx = _contexts()
    order = live_ctx.buy("MSFT", 10, order_type="LIMIT", limit_price=54.0)
    assert order is not None
    assert order.order_type is OrderType.LIMIT
    assert order.limit_price == 54.0


def test_unknown_order_type_raises():
    _, live_ctx = _contexts()
    with pytest.raises(ValueError, match="未知的订单类型"):
        live_ctx.buy("MSFT", 10, order_type="TELEPATHY")


def test_short_is_rejected_when_not_authorised():
    """做空在保证金模型落地前不对实盘开放 —— 显式抛错而非静默丢弃。"""
    _, live_ctx = _contexts()
    with pytest.raises(ValueError, match="做空"):
        live_ctx.short("MSFT", 10)


# ── 4. target_weight 与回测逐股一致 ───────────────────────────


def _order_tuples(orders) -> list[tuple]:
    return [(o.symbol, o.side.value, o.qty) for o in orders]


@pytest.mark.parametrize(
    "weights",
    [
        {"AAPL": 0.5, "MSFT": 0.5},
        {"AAPL": 0.2, "MSFT": 0.7},
        {"AAPL": 0.0, "MSFT": 0.9},
        {"AAPL": 1.0},
    ],
)
def test_target_weight_produces_identical_diff_orders(weights):
    backtest_ctx, live_ctx = _contexts()
    expected = _order_tuples(backtest_ctx.target_weight(weights))
    actual = _order_tuples(live_ctx.target_weight(weights))
    assert actual == expected
    assert expected, "夹具必须真的产生订单，否则本用例是空跑"


def test_target_weight_rejects_unknown_symbol_in_live_too():
    _, live_ctx = _contexts()
    with pytest.raises(ValueError, match="未纳入回测的标的"):
        live_ctx.target_weight({"TSLA": 1.0})


def test_target_weight_rejects_negative_weight_without_allow_short():
    _, live_ctx = _contexts()
    with pytest.raises(ValueError, match="allow_short"):
        live_ctx.target_weight({"AAPL": -0.5})


# ── 5. 快照校验：净值 0 绝不允许被当成合法状态 ────────────────


@pytest.mark.parametrize("bad_value", [0.0, -1.0, float("nan"), float("inf")])
def test_snapshot_rejects_non_positive_or_non_finite_portfolio_value(bad_value):
    with pytest.raises(ValueError, match="portfolio_value"):
        AccountSnapshot(
            cash=1000.0,
            portfolio_value=bad_value,
            positions={},
            prices={},
            taken_at=BASE_TIME,
        )


def test_snapshot_rejects_non_finite_cash():
    with pytest.raises(ValueError, match="cash"):
        AccountSnapshot(
            cash=float("nan"),
            portfolio_value=1000.0,
            positions={},
            prices={},
            taken_at=BASE_TIME,
        )


def test_snapshot_mappings_are_read_only():
    snap = AccountSnapshot(
        cash=1000.0,
        portfolio_value=2000.0,
        positions={"AAPL": 10},
        prices={"AAPL": 100.0},
        taken_at=BASE_TIME,
    )
    with pytest.raises(TypeError):
        snap.positions["AAPL"] = 20        # type: ignore[index]
    with pytest.raises(TypeError):
        snap.prices["AAPL"] = 1.0          # type: ignore[index]


def test_context_refuses_to_be_built_without_a_snapshot():
    with pytest.raises(TypeError):
        LivePortfolioContext(                # type: ignore[call-arg]
            time=BASE_TIME,
            bars={},
            symbols=list(SYMBOLS),
            histories=_histories(),
            market=Market.US,
        )
